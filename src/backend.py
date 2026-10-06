import sys
from pathlib import Path

# Add project root directory to python path for local imports
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import asyncio
import datetime
import logging
import shutil
import uuid
from typing import Optional
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.config import PROJECTS_DIR, DEFAULT_MODEL, DEFAULT_VOICE, BACKEND_HOST, BACKEND_PORT
from src.database import init_db, get_db, Project, Chapter, ProcessingJob, SessionLocal
from src.ollama_client import OllamaClient
from src.tts_client import TTSClient
from src.audio_utils import merge_wav_files, convert_wav_to_mp3, get_wav_duration
from src.document_utils import (
    auto_detect_chapter_title, export_to_txt, export_to_pdf, export_to_docx
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(PROJECTS_DIR.parent / "logs" / "application.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("backend")

# Initialize DB tables
init_db()

app = FastAPI(title="AI Novel Condenser & Audiobook Generator API")

# Shared clients
ollama_client = OllamaClient()
tts_client = TTSClient()

# Core locks to prevent concurrent execution on the same resource
job_locks = {}

# --- Pydantic Schemas ---

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    model_name: str = "qwen2.5:7b-instruct"
    temperature: float = 0.5
    max_tokens: int = 4096  # Increased default; dynamic calculation will override
    tts_voice: str = "af_bella"
    tts_speed: float = 1.0
    tts_volume: float = 1.0
    audio_format: str = "wav"

class ProjectUpdate(BaseModel):
    model_name: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    tts_voice: Optional[str] = None
    tts_speed: Optional[float] = None
    tts_volume: Optional[float] = None
    audio_format: Optional[str] = None

class ChapterCreate(BaseModel):
    title: Optional[str] = None
    original_text: str = Field(..., min_length=1)

class ChapterUpdate(BaseModel):
    title: Optional[str] = None
    condensed_text: Optional[str] = None
    order_index: Optional[int] = None
    status: Optional[str] = None

class ScrapeRequest(BaseModel):
    url: str = Field(..., min_length=1)
    
class ScrapeBulkRequest(BaseModel):
    url: str = Field(..., min_length=1)
    max_chapters: int = Field(default=50, ge=1, le=200)
    delay: float = Field(default=1.5, ge=0.5, le=10.0)

# --- Background Task Workers ---

async def run_condensation_task(job_id: str, project_id: int, chapter_id: int):
    """Worker task that runs Ollama condensation and updates the DB."""
    db: Session = next(get_db())
    job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
    chapter = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    project = db.query(Project).filter(Project.id == project_id).first()
    
    if not job or not chapter or not project:
        logger.error(f"Condensation task failed: resources not found in DB. Job: {job_id}")
        return

    job.status = "running"
    job.progress = 10.0
    chapter.status = "condensing"
    db.commit()

    try:
        # Check if Ollama is online
        if not await ollama_client.check_status():
            raise ConnectionError(
                "Ollama is offline or unreachable. Please verify Ollama is running locally."
            )

        model_name = project.model_name
        installed_models = await ollama_client.list_local_models()
        if not ollama_client._model_is_installed(model_name, installed_models):
            logger.info(f"Model '{model_name}' missing locally. Auto-pulling from Ollama...")

            def pull_progress(msg, pct):
                job.error_message = msg
                job.progress = 10.0 + (pct * 0.2)
                db.commit()

            await ollama_client.pull_model(model_name, progress_callback=pull_progress)
            job.error_message = None

        logger.info(f"Starting condensation for chapter {chapter.title} (ID: {chapter.id})")
        job.progress = 30.0
        db.commit()
        
        # Call Ollama with dynamic max_tokens (calculated inside ollama_client)
        condensed = await ollama_client.condense_chapter(
            chapter_text=chapter.original_text,
            model=project.model_name,
            temperature=project.temperature,
            max_tokens=project.max_tokens
        )
        
        # Save results
        chapter.condensed_text = condensed
        chapter.word_count_condensed = len(condensed.split())
        chapter.status = "condensed"
        
        # Save to projects file system as well
        proj_dir = PROJECTS_DIR / project.name
        cond_dir = proj_dir / "condensed"
        cond_dir.mkdir(parents=True, exist_ok=True)
        
        filename = f"chapter{chapter.order_index:03d}.txt"
        with open(cond_dir / filename, "w", encoding="utf-8") as f:
            f.write(condensed)

        job.status = "completed"
        job.progress = 100.0
        logger.info(f"Finished condensation for chapter {chapter.title} (ID: {chapter.id})")

    except Exception as e:
        logger.exception(f"Error in condensation worker for chapter {chapter.title}")
        job.status = "failed"
        job.error_message = str(e)
        chapter.status = "failed"
    finally:
        db.commit()
        db.close()

async def run_tts_task(job_id: str, project_id: int, chapter_id: int):
    """Worker task that runs Kokoro TTS and updates the DB."""
    db: Session = next(get_db())
    job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
    chapter = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    project = db.query(Project).filter(Project.id == project_id).first()
    
    if not job or not chapter or not project:
        logger.error(f"TTS task failed: resources not found in DB. Job: {job_id}")
        return

    job.status = "running"
    job.progress = 5.0
    chapter.status = "generating_audio"
    db.commit()

    try:
        # Check and download models if necessary (blocking safety check)
        if not tts_client.is_model_downloaded():
            logger.info("TTS model files missing. Commencing auto-download...")
            
            def download_progress(msg, pct):
                job.error_message = msg
                # Map the download progress (0-100%) to overall task progress (5-90%)
                job.progress = 5.0 + (pct * 0.85)
                # Keep committing progress
                db.commit()

            await tts_client.download_model_files(progress_callback=download_progress)
            job.error_message = None
            db.commit()

        # Build paths
        proj_dir = PROJECTS_DIR / project.name
        audio_dir = proj_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        
        wav_path = audio_dir / f"chapter{chapter.order_index:03d}.wav"
        
        # Check if condensed text is available
        text_to_speak = chapter.condensed_text or chapter.original_text
        if not text_to_speak.strip():
            raise ValueError("Chapter text is empty.")

        logger.info(f"Starting TTS generation for chapter {chapter.title} (ID: {chapter.id})")
        
        # Progress callback for chunk generation
        def tts_progress(msg, pct):
            job.progress = 10.0 + (pct * 0.85)  # Scale between 10% and 95%
            db.commit()

        # Generate WAV using soundfile — run in thread to prevent event loop blocking
        duration = await asyncio.to_thread(
            tts_client.generate_audio,
            text=text_to_speak,
            output_path=wav_path,
            voice=project.tts_voice,
            speed=project.tts_speed,
            volume=project.tts_volume,
            progress_callback=tts_progress
        )

        chapter.audio_path = str(wav_path)
        chapter.audio_duration = duration
        chapter.status = "completed"

        job.status = "completed"
        job.progress = 100.0
        logger.info(f"Finished TTS generation for chapter {chapter.title} (ID: {chapter.id})")

    except Exception as e:
        logger.exception(f"Error in TTS worker for chapter {chapter.title}")
        job.status = "failed"
        job.error_message = str(e)
        chapter.status = "failed"
    finally:
        db.commit()
        db.close()

async def run_audiobook_task(job_id: str, project_id: int, pause_duration: float):
    """Worker task that compiles the final audiobook from chapters."""
    db: Session = next(get_db())
    job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
    project = db.query(Project).filter(Project.id == project_id).first()
    
    if not job or not project:
        logger.error(f"Audiobook compilation failed: resources not found. Job: {job_id}")
        return

    job.status = "running"
    job.progress = 10.0
    db.commit()

    try:
        # Get all completed chapters with audio
        chapters = db.query(Chapter).filter(
            Chapter.project_id == project_id,
            Chapter.audio_path != None
        ).order_by(Chapter.order_index.asc()).all()

        if not chapters:
            raise ValueError("No chapters with generated audio found. Please generate audio first.")

        proj_dir = PROJECTS_DIR / project.name
        exports_dir = proj_dir / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        
        final_wav_path = exports_dir / "audiobook.wav"
        
        # Audio inputs
        input_paths = [Path(ch.audio_path) for ch in chapters]
        
        job.progress = 30.0
        db.commit()

        # Merge WAV files
        await asyncio.to_thread(merge_wav_files, input_paths, final_wav_path, pause_duration)
        
        job.progress = 70.0
        db.commit()

        # If project audio format is MP3, try converting
        if project.audio_format == "mp3":
            final_mp3_path = exports_dir / "audiobook.mp3"
            success = await asyncio.to_thread(convert_wav_to_mp3, final_wav_path, final_mp3_path)
            if success:
                logger.info("Successfully converted audiobook to MP3 format.")
                # We keep WAV but MP3 is preferred if user wants it
            else:
                logger.warning("Failed to convert WAV to MP3. Defaulting to WAV.")

        job.status = "completed"
        job.progress = 100.0
        logger.info(f"Finished audiobook compilation for project {project.name}")

    except Exception as e:
        logger.exception(f"Error in audiobook compilation for project {project.name}")
        job.status = "failed"
        job.error_message = str(e)
    finally:
        db.commit()
        db.close()

async def run_scrape_task(job_id: str, project_id: int, url: str, is_bulk: bool = False, max_chapters: int = 50, delay: float = 1.5):
    """Worker task that scrapes web content and creates chapters."""
    db: Session = next(get_db())
    job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
    project = db.query(Project).filter(Project.id == project_id).first()
    
    if not job or not project:
        logger.error(f"Scrape task failed: resources not found. Job: {job_id}")
        return

    job.status = "running"
    job.progress = 5.0
    db.commit()

    try:
        from src.scraper import WebScraper
        scraper = WebScraper()
        
        if is_bulk:
            # Scrape table of contents and import all chapters
            logger.info(f"Starting bulk scrape from: {url}")
            job.error_message = "Detecting chapter links..."
            db.commit()
            
            chapter_links = await scraper.scrape_chapter_list(url)
            if not chapter_links:
                raise ValueError("No chapter links found on the provided URL. Try scraping as a single page instead.")
            
            # Limit chapters
            chapter_links = chapter_links[:max_chapters]
            total = len(chapter_links)
            logger.info(f"Found {total} chapter links to scrape")
            
            imported = 0
            for idx, link_info in enumerate(chapter_links):
                job.progress = 5.0 + ((idx / total) * 90.0)
                job.error_message = f"Scraping chapter {idx+1}/{total}: {link_info.get('title', 'Unknown')}..."
                db.commit()
                
                try:
                    scraped = await scraper.scrape_single_page(link_info["url"])
                    if scraped and scraped.get("content", "").strip():
                        # Determine order index
                        last_ch = db.query(Chapter).filter(
                            Chapter.project_id == project_id
                        ).order_by(Chapter.order_index.desc()).first()
                        next_index = (last_ch.order_index + 1) if last_ch else 1
                        
                        title = scraped.get("title") or link_info.get("title") or f"Chapter {next_index}"
                        
                        # Save original to disk
                        proj_dir = PROJECTS_DIR / project.name
                        orig_dir = proj_dir / "original"
                        orig_dir.mkdir(parents=True, exist_ok=True)
                        fname = f"chapter{next_index:03d}.txt"
                        with open(orig_dir / fname, "w", encoding="utf-8") as f:
                            f.write(scraped["content"])
                        
                        db_chapter = Chapter(
                            project_id=project_id,
                            title=title,
                            order_index=next_index,
                            original_text=scraped["content"],
                            word_count_original=len(scraped["content"].split()),
                            status="draft"
                        )
                        db.add(db_chapter)
                        db.commit()
                        imported += 1
                        
                except Exception as chapter_err:
                    logger.warning(f"Failed to scrape chapter {idx+1}: {str(chapter_err)}")
                    continue
                
                # Rate limiting
                if idx < total - 1:
                    await asyncio.sleep(delay)
            
            job.error_message = f"Successfully imported {imported}/{total} chapters"
            
        else:
            # Single page scrape
            logger.info(f"Scraping single page: {url}")
            job.error_message = "Extracting content from URL..."
            job.progress = 20.0
            db.commit()
            
            scraped = await scraper.scrape_single_page(url)
            if not scraped or not scraped.get("content", "").strip():
                raise ValueError("Could not extract any content from the provided URL.")
            
            job.progress = 70.0
            db.commit()
            
            # Create chapter
            last_ch = db.query(Chapter).filter(
                Chapter.project_id == project_id
            ).order_by(Chapter.order_index.desc()).first()
            next_index = (last_ch.order_index + 1) if last_ch else 1
            
            title = scraped.get("title") or auto_detect_chapter_title(scraped["content"], f"Chapter {next_index}")
            
            # Save to disk
            proj_dir = PROJECTS_DIR / project.name
            orig_dir = proj_dir / "original"
            orig_dir.mkdir(parents=True, exist_ok=True)
            fname = f"chapter{next_index:03d}.txt"
            with open(orig_dir / fname, "w", encoding="utf-8") as f:
                f.write(scraped["content"])
            
            db_chapter = Chapter(
                project_id=project_id,
                title=title,
                order_index=next_index,
                original_text=scraped["content"],
                word_count_original=len(scraped["content"].split()),
                status="draft"
            )
            db.add(db_chapter)
            db.commit()
            
            job.error_message = f"Imported: {title} ({scraped.get('word_count', 0)} words)"
        
        await scraper.close()
        
        job.status = "completed"
        job.progress = 100.0
        logger.info(f"Finished scrape task for project {project.name}")

    except Exception as e:
        logger.exception(f"Error in scrape worker for project {project.name}")
        job.status = "failed"
        job.error_message = str(e)
    finally:
        db.commit()
        db.close()

# --- Auto Resume on Startup ---

@app.on_event("startup")
async def resume_interrupted_jobs():
    """Scans the DB on startup and automatically resumes pending/running jobs."""
    db = next(get_db())
    interrupted_jobs = db.query(ProcessingJob).filter(
        ProcessingJob.status.in_(["pending", "running"])
    ).all()
    
    if interrupted_jobs:
        logger.info(f"Found {len(interrupted_jobs)} interrupted jobs. Auto-resuming...")
        
    for job in interrupted_jobs:
        job_id = job.id
        project_id = job.project_id
        chapter_id = job.chapter_id
        job_type = job.job_type
        
        # Reset status
        job.status = "pending"
        db.commit()
        
        # Restart background tasks
        if job_type == "condense" and chapter_id:
            asyncio.create_task(run_condensation_task(job_id, project_id, chapter_id))
        elif job_type == "tts" and chapter_id:
            asyncio.create_task(run_tts_task(job_id, project_id, chapter_id))
        elif job_type == "audiobook":
            # Default to 2.0s pause if resuming
            asyncio.create_task(run_audiobook_task(job_id, project_id, 2.0))
        elif job_type == "pull_model":
            project = db.query(Project).filter(Project.id == project_id).first()
            if project:
                asyncio.create_task(run_pull_model_task(job_id, project_id, project.model_name))
            
    db.close()

# --- API Endpoints ---

@app.post("/project", status_code=status.HTTP_201_CREATED)
def create_project(proj: ProjectCreate, db: Session = Depends(get_db)):
    """Creates a new project and sets up directories."""
    # Check if name exists
    existing = db.query(Project).filter(Project.name == proj.name).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Project with name '{proj.name}' already exists."
        )

    # Create directories
    proj_dir = PROJECTS_DIR / proj.name
    for sub in ["original", "condensed", "audio", "exports"]:
        (proj_dir / sub).mkdir(parents=True, exist_ok=True)

    db_proj = Project(
        name=proj.name,
        model_name=proj.model_name,
        temperature=proj.temperature,
        max_tokens=proj.max_tokens,
        tts_voice=proj.tts_voice,
        tts_speed=proj.tts_speed,
        tts_volume=proj.tts_volume,
        audio_format=proj.audio_format
    )
    db.add(db_proj)
    db.commit()
    db.refresh(db_proj)
    logger.info(f"Created project: {db_proj.name} (ID: {db_proj.id})")
    return db_proj.to_dict()

@app.get("/projects")
def list_projects(db: Session = Depends(get_db)):
    """Lists all available projects."""
    projects = db.query(Project).all()
    return [p.to_dict() for p in projects]

@app.get("/project/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db)):
    """Retrieves full project details including chapters and statistics."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    chapters = db.query(Chapter).filter(Chapter.project_id == project_id).order_by(Chapter.order_index.asc()).all()
    jobs = db.query(ProcessingJob).filter(ProcessingJob.project_id == project_id).all()
    
    # Calculate statistics
    words_original = sum(ch.word_count_original for ch in chapters)
    words_condensed = sum(ch.word_count_condensed for ch in chapters)
    audio_duration = sum(ch.audio_duration for ch in chapters)
    
    data = project.to_dict()
    data["chapters"] = [ch.to_dict() for ch in chapters]
    data["jobs"] = [job.to_dict() for job in jobs]
    data["statistics"] = {
        "chapters_count": len(chapters),
        "words_original": words_original,
        "words_condensed": words_condensed,
        "audio_duration_seconds": audio_duration,
        "compression_percentage": round((1 - (words_condensed / words_original)) * 100, 1) if words_original > 0 else 0.0
    }
    return data

@app.put("/project/{project_id}")
def update_project(project_id: int, update: ProjectUpdate, db: Session = Depends(get_db)):
    """Updates global settings of a project."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    for key, value in update.dict(exclude_unset=True).items():
        setattr(project, key, value)
    db.commit()
    return project.to_dict()

@app.delete("/project/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    """Deletes a project from the DB and disk."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Delete files
    proj_dir = PROJECTS_DIR / project.name
    if proj_dir.exists():
        try:
            shutil.rmtree(proj_dir)
        except Exception as e:
            logger.error(f"Failed to delete directory {proj_dir}: {str(e)}")

    db.delete(project)
    db.commit()
    logger.info(f"Deleted project: {project.name} (ID: {project_id})")
    return {"message": "Project deleted successfully"}

# --- Chapter Endpoints ---

@app.post("/project/{project_id}/chapter")
def add_chapter(project_id: int, ch: ChapterCreate, db: Session = Depends(get_db)):
    """Adds a new chapter to the project."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Determine order index
    last_chapter = db.query(Chapter).filter(Chapter.project_id == project_id).order_by(Chapter.order_index.desc()).first()
    next_index = (last_chapter.order_index + 1) if last_chapter else 1

    # Detect title if not provided
    title = ch.title
    if not title:
        title = auto_detect_chapter_title(ch.original_text, f"Chapter {next_index}")

    # Write original text to disk
    proj_dir = PROJECTS_DIR / project.name
    orig_dir = proj_dir / "original"
    orig_dir.mkdir(parents=True, exist_ok=True)
    
    filename = f"chapter{next_index:03d}.txt"
    with open(orig_dir / filename, "w", encoding="utf-8") as f:
        f.write(ch.original_text)

    db_chapter = Chapter(
        project_id=project_id,
        title=title,
        order_index=next_index,
        original_text=ch.original_text,
        word_count_original=len(ch.original_text.split()),
        status="draft"
    )
    db.add(db_chapter)
    db.commit()
    db.refresh(db_chapter)
    logger.info(f"Added chapter: {db_chapter.title} (ID: {db_chapter.id}) to project {project.name}")
    return db_chapter.to_dict()

@app.put("/chapter/{chapter_id}")
def update_chapter(chapter_id: int, update: ChapterUpdate, db: Session = Depends(get_db)):
    """Updates a chapter's title, text, or order index."""
    chapter = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    project = db.query(Project).filter(Project.id == chapter.project_id).first()

    old_index = chapter.order_index

    # Check for text modifications to update word count and save to file
    for key, val in update.dict(exclude_unset=True).items():
        if key == "condensed_text":
            # Track history before updating
            history = chapter.edit_history
            history.append({
                "timestamp": datetime.datetime.utcnow().isoformat(),
                "previous_length": len(chapter.condensed_text or ""),
                "action": "edit_condensed"
            })
            chapter.edit_history = history
            chapter.condensed_text = val
            chapter.word_count_condensed = len(val.split()) if val else 0
            
            # Save edited condensed text to file
            if val and project:
                cond_dir = PROJECTS_DIR / project.name / "condensed"
                cond_dir.mkdir(parents=True, exist_ok=True)
                with open(cond_dir / f"chapter{chapter.order_index:03d}.txt", "w", encoding="utf-8") as f:
                    f.write(val)
        else:
            setattr(chapter, key, val)

    # Handle index changes (shifting others)
    if update.order_index is not None and update.order_index != old_index:
        new_index = update.order_index
        # Shift chapters in between
        if new_index > old_index:
            db.query(Chapter).filter(
                Chapter.project_id == chapter.project_id,
                Chapter.order_index > old_index,
                Chapter.order_index <= new_index,
                Chapter.id != chapter.id
            ).update({Chapter.order_index: Chapter.order_index - 1}, synchronize_session=False)
        else:
            db.query(Chapter).filter(
                Chapter.project_id == chapter.project_id,
                Chapter.order_index >= new_index,
                Chapter.order_index < old_index,
                Chapter.id != chapter.id
            ).update({Chapter.order_index: Chapter.order_index + 1}, synchronize_session=False)
            
        chapter.order_index = new_index

    db.commit()
    db.refresh(chapter)
    return chapter.to_dict()

@app.delete("/chapter/{chapter_id}")
def delete_chapter(chapter_id: int, db: Session = Depends(get_db)):
    """Deletes a chapter and removes its associated files."""
    chapter = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    project = db.query(Project).filter(Project.id == chapter.project_id).first()
    order_index = chapter.order_index

    # Remove files
    if project:
        proj_dir = PROJECTS_DIR / project.name
        
        orig_file = proj_dir / "original" / f"chapter{order_index:03d}.txt"
        cond_file = proj_dir / "condensed" / f"chapter{order_index:03d}.txt"
        audio_file = proj_dir / "audio" / f"chapter{order_index:03d}.wav"
        
        for f in [orig_file, cond_file, audio_file]:
            if f.exists():
                try:
                    f.unlink()
                except Exception as e:
                    logger.error(f"Failed to delete file {f}: {str(e)}")

    # Shift subsequent chapters down
    db.query(Chapter).filter(
        Chapter.project_id == chapter.project_id,
        Chapter.order_index > order_index
    ).update({Chapter.order_index: Chapter.order_index - 1}, synchronize_session=False)

    db.delete(chapter)
    db.commit()
    logger.info(f"Deleted chapter: {chapter.title} (ID: {chapter_id})")
    return {"message": "Chapter deleted successfully"}

# --- Action Trigger Endpoints ---

@app.post("/chapter/{chapter_id}/condense")
async def trigger_condensation(chapter_id: int, db: Session = Depends(get_db)):
    """Triggers background condensation for a single chapter."""
    chapter = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    # Clean existing failed/running jobs for this chapter
    db.query(ProcessingJob).filter(
        ProcessingJob.chapter_id == chapter_id,
        ProcessingJob.job_type == "condense"
    ).delete()

    job_id = f"condense-{uuid.uuid4().hex[:8]}"
    db_job = ProcessingJob(
        id=job_id,
        project_id=chapter.project_id,
        chapter_id=chapter_id,
        job_type="condense",
        status="pending",
        progress=0.0
    )
    db.add(db_job)
    chapter.status = "condensing"
    db.commit()

    # Schedule background worker
    asyncio.create_task(run_condensation_task(job_id, chapter.project_id, chapter_id))
    
    return db_job.to_dict()

@app.post("/chapter/{chapter_id}/tts")
async def trigger_tts(chapter_id: int, db: Session = Depends(get_db)):
    """Triggers background TTS generation for a single chapter."""
    chapter = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    # Clean existing jobs
    db.query(ProcessingJob).filter(
        ProcessingJob.chapter_id == chapter_id,
        ProcessingJob.job_type == "tts"
    ).delete()

    job_id = f"tts-{uuid.uuid4().hex[:8]}"
    db_job = ProcessingJob(
        id=job_id,
        project_id=chapter.project_id,
        chapter_id=chapter_id,
        job_type="tts",
        status="pending",
        progress=0.0
    )
    db.add(db_job)
    chapter.status = "generating_audio"
    db.commit()

    # Schedule background worker
    asyncio.create_task(run_tts_task(job_id, chapter.project_id, chapter_id))
    
    return db_job.to_dict()

@app.post("/project/{project_id}/audiobook")
async def trigger_audiobook(project_id: int, pause_duration: float = 2.0, db: Session = Depends(get_db)):
    """Triggers background compilation of all chapter audio files."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Clean existing audiobook jobs
    db.query(ProcessingJob).filter(
        ProcessingJob.project_id == project_id,
        ProcessingJob.job_type == "audiobook"
    ).delete()

    job_id = f"audiobook-{uuid.uuid4().hex[:8]}"
    db_job = ProcessingJob(
        id=job_id,
        project_id=project_id,
        job_type="audiobook",
        status="pending",
        progress=0.0
    )
    db.add(db_job)
    db.commit()

    asyncio.create_task(run_audiobook_task(job_id, project_id, pause_duration))
    
    return db_job.to_dict()

# --- Web Scraping Endpoints ---

@app.post("/project/{project_id}/scrape")
async def trigger_scrape(project_id: int, req: ScrapeRequest, db: Session = Depends(get_db)):
    """Scrapes a single URL and imports the content as a new chapter."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    job_id = f"scrape-{uuid.uuid4().hex[:8]}"
    db_job = ProcessingJob(
        id=job_id,
        project_id=project_id,
        job_type="scrape",
        status="pending",
        progress=0.0
    )
    db.add(db_job)
    db.commit()

    asyncio.create_task(run_scrape_task(job_id, project_id, req.url, is_bulk=False))
    
    return db_job.to_dict()

@app.post("/project/{project_id}/scrape-bulk")
async def trigger_bulk_scrape(project_id: int, req: ScrapeBulkRequest, db: Session = Depends(get_db)):
    """Scrapes a table-of-contents URL and imports all detected chapters."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    job_id = f"scrape-bulk-{uuid.uuid4().hex[:8]}"
    db_job = ProcessingJob(
        id=job_id,
        project_id=project_id,
        job_type="scrape",
        status="pending",
        progress=0.0
    )
    db.add(db_job)
    db.commit()

    asyncio.create_task(run_scrape_task(
        job_id, project_id, req.url, 
        is_bulk=True, max_chapters=req.max_chapters, delay=req.delay
    ))
    
    return db_job.to_dict()

@app.get("/scrape/preview")
async def preview_scrape(url: str):
    """Preview scraped content from a URL without importing."""
    try:
        from src.scraper import WebScraper
        scraper = WebScraper()
        result = await scraper.preview_url(url)
        await scraper.close()
        
        if not result:
            raise HTTPException(status_code=400, detail="Could not extract content from URL")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scraping error: {str(e)}")

# --- Export & Download Endpoints ---

@app.get("/project/{project_id}/export/{format}")
def download_export(project_id: int, format: str, db: Session = Depends(get_db)):
    """
    Generates and returns the compiled book file in TXT, PDF, or DOCX.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    chapters = db.query(Chapter).filter(Chapter.project_id == project_id).order_by(Chapter.order_index.asc()).all()
    if not chapters:
        raise HTTPException(status_code=400, detail="Cannot export an empty project.")

    proj_dir = PROJECTS_DIR / project.name
    exports_dir = proj_dir / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)

    chapters_data = [{"title": ch.title, "condensed_text": ch.condensed_text} for ch in chapters]

    if format.lower() == "txt":
        dest_file = exports_dir / "condensed.txt"
        export_to_txt(dest_file, chapters_data)
        media_type = "text/plain"
    elif format.lower() == "pdf":
        dest_file = exports_dir / "condensed.pdf"
        export_to_pdf(dest_file, chapters_data, project.name)
        media_type = "application/pdf"
    elif format.lower() == "docx":
        dest_file = exports_dir / "condensed.docx"
        export_to_docx(dest_file, chapters_data, project.name)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {format}")

    return FileResponse(
        path=str(dest_file),
        media_type=media_type,
        filename=dest_file.name
    )

@app.get("/project/{project_id}/audiobook/download")
def download_audiobook(project_id: int, db: Session = Depends(get_db)):
    """Downloads the compiled audiobook file."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    exports_dir = PROJECTS_DIR / project.name / "exports"
    
    # Check for MP3 first, then WAV
    mp3_file = exports_dir / "audiobook.mp3"
    wav_file = exports_dir / "audiobook.wav"

    if mp3_file.exists():
        return FileResponse(path=str(mp3_file), media_type="audio/mpeg", filename=f"{project.name}_audiobook.mp3")
    elif wav_file.exists():
        return FileResponse(path=str(wav_file), media_type="audio/wav", filename=f"{project.name}_audiobook.wav")
    else:
        raise HTTPException(status_code=404, detail="Audiobook file not compiled yet. Please run compilation.")

@app.get("/chapter/{chapter_id}/audio/download")
def download_chapter_audio(chapter_id: int, db: Session = Depends(get_db)):
    """Downloads a single chapter's audio file."""
    chapter = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not chapter or not chapter.audio_path:
        raise HTTPException(status_code=404, detail="Audio not found for this chapter")

    path = Path(chapter.audio_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Audio file missing from disk")

    return FileResponse(path=str(path), media_type="audio/wav", filename=f"{chapter.title}.wav")

# --- TTS Voice Test Endpoint ---

@app.get("/tts/test")
async def test_tts_voice(voice: str = DEFAULT_VOICE, speed: float = 1.0):
    """Generates a short test clip to verify TTS is working."""
    try:
        if not tts_client.is_model_downloaded():
            raise HTTPException(status_code=400, detail="TTS model files not downloaded yet.")
        
        samples, sr = await asyncio.to_thread(tts_client.test_voice, voice, speed)
        
        # Save to temp file and return
        import tempfile
        temp_path = Path(tempfile.mktemp(suffix=".wav"))
        import soundfile as sf
        sf.write(str(temp_path), samples, sr)
        
        return FileResponse(
            path=str(temp_path), 
            media_type="audio/wav", 
            filename=f"voice_test_{voice}.wav"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Voice test failed: {str(e)}")

# --- Ollama Pull & Dynamic Models Endpoints ---

async def run_pull_model_task(job_id: str, project_id: int, model_name: str):
    """Worker task that downloads a model from Ollama registry."""
    db = SessionLocal()
    job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
    
    if not job:
        logger.error(f"Ollama pull model task failed: job {job_id} not found in DB.")
        db.close()
        return

    job.status = "running"
    job.progress = 0.0
    db.commit()

    try:
        # Check if Ollama is online
        if not await ollama_client.check_status():
            raise ConnectionError(
                "Ollama is offline. Please launch the Ollama application locally."
            )

        logger.info(f"Starting background model pull for '{model_name}' (Job: {job_id})")
        
        def pull_progress(msg, pct):
            job.error_message = msg
            job.progress = pct
            db.commit()

        await ollama_client.pull_model(model_name, progress_callback=pull_progress)

        job.status = "completed"
        job.progress = 100.0
        job.error_message = None
        logger.info(f"Finished pulling model '{model_name}'")

    except Exception as e:
        logger.exception(f"Error in model pull worker for '{model_name}'")
        job.status = "failed"
        job.error_message = str(e)
    finally:
        db.commit()
        db.close()

@app.get("/ollama/models")
async def get_ollama_models():
    """Lists downloaded models in the local Ollama instance."""
    models = await ollama_client.list_local_models()
    return {"models": models}

@app.post("/project/{project_id}/pull-model")
async def trigger_model_pull(project_id: int, db: Session = Depends(get_db)):
    """Triggers background pull of the project's model from Ollama registry."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    model_name = project.model_name
    
    # Delete any existing pull jobs for this project
    db.query(ProcessingJob).filter(
        ProcessingJob.project_id == project_id,
        ProcessingJob.job_type == "pull_model"
    ).delete()

    job_id = f"pull-{uuid.uuid4().hex[:8]}"
    db_job = ProcessingJob(
        id=job_id,
        project_id=project_id,
        job_type="pull_model",
        status="pending",
        progress=0.0
    )
    db.add(db_job)
    db.commit()

    # Schedule background worker
    asyncio.create_task(run_pull_model_task(job_id, project_id, model_name))
    
    return db_job.to_dict()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend:app", host=BACKEND_HOST, port=BACKEND_PORT, reload=True)
