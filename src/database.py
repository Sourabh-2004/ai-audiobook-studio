import datetime
import json
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime, Text, ForeignKey
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from src.config import DATABASE_URL

Base = declarative_base()

class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    
    # AI Settings
    model_name = Column(String, default="qwen2.5:7b-instruct")
    temperature = Column(Float, default=0.5)
    max_tokens = Column(Integer, default=2048)
    
    # TTS Settings
    tts_voice = Column(String, default="af_bella")
    tts_speed = Column(Float, default=1.0)
    tts_volume = Column(Float, default=1.0)
    audio_format = Column(String, default="wav")  # "wav" or "mp3"
    
    # Relationships
    chapters = relationship("Chapter", back_populates="project", cascade="all, delete-orphan")
    jobs = relationship("ProcessingJob", back_populates="project", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "model_name": self.model_name,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "tts_voice": self.tts_voice,
            "tts_speed": self.tts_speed,
            "tts_volume": self.tts_volume,
            "audio_format": self.audio_format,
        }

class Chapter(Base):
    __tablename__ = "chapters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    title = Column(String, nullable=False)
    order_index = Column(Integer, nullable=False)
    
    # Content
    original_text = Column(Text, nullable=False)
    condensed_text = Column(Text, nullable=True)
    
    # Metrics
    word_count_original = Column(Integer, default=0)
    word_count_condensed = Column(Integer, default=0)
    
    # Status
    # "draft", "condensing", "condensed", "generating_audio", "completed", "failed"
    status = Column(String, default="draft")
    
    # Audio
    audio_path = Column(String, nullable=True)
    audio_duration = Column(Float, default=0.0)  # in seconds
    
    # History
    edit_history_json = Column(Text, default="[]")  # List of JSON objects of changes
    
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    project = relationship("Project", back_populates="chapters")
    jobs = relationship("ProcessingJob", back_populates="chapter", cascade="all, delete-orphan")

    @property
    def edit_history(self):
        try:
            return json.loads(self.edit_history_json or "[]")
        except Exception:
            return []

    @edit_history.setter
    def edit_history(self, val):
        self.edit_history_json = json.dumps(val)

    def to_dict(self):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "order_index": self.order_index,
            "original_text": self.original_text,
            "condensed_text": self.condensed_text,
            "word_count_original": self.word_count_original,
            "word_count_condensed": self.word_count_condensed,
            "status": self.status,
            "audio_path": self.audio_path,
            "audio_duration": self.audio_duration,
            "edit_history": self.edit_history,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id = Column(String, primary_key=True)  # unique task identifier (UUID or custom string)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    chapter_id = Column(Integer, ForeignKey("chapters.id"), nullable=True)
    
    job_type = Column(String, nullable=False)  # "condense", "tts", "audiobook"
    status = Column(String, default="pending")  # "pending", "running", "completed", "failed"
    progress = Column(Float, default=0.0)  # 0.0 to 100.0
    error_message = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    project = relationship("Project", back_populates="jobs")
    chapter = relationship("Chapter", back_populates="jobs")

    def to_dict(self):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "chapter_id": self.chapter_id,
            "job_type": self.job_type,
            "status": self.status,
            "progress": self.progress,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

# Database engine & session initialization
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
