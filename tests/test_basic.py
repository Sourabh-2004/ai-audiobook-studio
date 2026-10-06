import unittest
import tempfile
import os
import wave
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Import project modules
from src.database import Base, Project, Chapter, ProcessingJob
from src.document_utils import (
    extract_text_from_txt, auto_detect_chapter_title,
    export_to_txt, export_to_pdf, export_to_docx
)
from src.audio_utils import merge_wav_files, get_wav_duration, generate_wav_silence

class TestDatabase(unittest.TestCase):
    def setUp(self):
        # Set up an in-memory database for testing
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)

    def test_project_and_chapter_creation(self):
        # Create Project
        proj = Project(
            name="Test Book",
            model_name="qwen2.5:7b-instruct",
            temperature=0.5,
            tts_voice="af_bella"
        )
        self.db.add(proj)
        self.db.commit()
        
        self.assertEqual(proj.id, 1)
        self.assertEqual(proj.name, "Test Book")
        
        # Create Chapter
        ch = Chapter(
            project_id=proj.id,
            title="Chapter 1: The Beginning",
            order_index=1,
            original_text="Once upon a time in a test environment...",
            word_count_original=8,
            status="draft"
        )
        self.db.add(ch)
        self.db.commit()
        
        self.assertEqual(ch.id, 1)
        self.assertEqual(ch.project_id, 1)
        self.assertEqual(ch.word_count_original, 8)
        
        # Test Relationship
        self.assertEqual(len(proj.chapters), 1)
        self.assertEqual(proj.chapters[0].title, "Chapter 1: The Beginning")

class TestDocumentUtils(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_txt_extraction(self):
        txt_file = self.temp_path / "test.txt"
        test_content = "This is a test chapter content.\nLine two."
        with open(txt_file, "w", encoding="utf-8") as f:
            f.write(test_content)
            
        extracted = extract_text_from_txt(txt_file)
        self.assertEqual(extracted, test_content)

    def test_chapter_title_detection(self):
        text_with_title = "Chapter 12: A Wild Adventure\nThe sun was shining bright..."
        title = auto_detect_chapter_title(text_with_title)
        self.assertEqual(title, "Chapter 12: A Wild Adventure")
        
        text_no_title = "The sun rose slowly over the mountains, casting long shadows across the valley."
        title_default = auto_detect_chapter_title(text_no_title, default_title="Fallback Title")
        self.assertEqual(title_default, "Fallback Title")

    def test_document_exporters(self):
        chapters_data = [
            {"title": "Chapter 1", "condensed_text": "Condensed content for chapter 1."},
            {"title": "Chapter 2", "condensed_text": "Condensed content for chapter 2."}
        ]
        
        # Test TXT Export
        txt_dest = self.temp_path / "book.txt"
        export_to_txt(txt_dest, chapters_data)
        self.assertTrue(txt_dest.exists())
        self.assertGreater(txt_dest.stat().st_size, 0)
        
        # Test PDF Export
        pdf_dest = self.temp_path / "book.pdf"
        export_to_pdf(pdf_dest, chapters_data, "Test Book Title")
        self.assertTrue(pdf_dest.exists())
        self.assertGreater(pdf_dest.stat().st_size, 0)
        
        # Test DOCX Export
        docx_dest = self.temp_path / "book.docx"
        export_to_docx(docx_dest, chapters_data, "Test Book Title")
        self.assertTrue(docx_dest.exists())
        self.assertGreater(docx_dest.stat().st_size, 0)

class TestAudioUtils(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        
        # Generate a dummy WAV file (1 second, 24kHz, 16-bit mono)
        self.sample_rate = 24000
        self.channels = 1
        self.sample_width = 2
        self.duration = 1.0  # 1 second
        
        self.dummy_wav1 = self.temp_path / "dummy1.wav"
        self.dummy_wav2 = self.temp_path / "dummy2.wav"
        
        for path in [self.dummy_wav1, self.dummy_wav2]:
            with wave.open(str(path), "wb") as w:
                w.setnchannels(self.channels)
                w.setsampwidth(self.sample_width)
                w.setframerate(self.sample_rate)
                # Zeros are silence
                silence_data = generate_wav_silence(self.duration, self.sample_rate, self.channels, self.sample_width)
                w.writeframes(silence_data)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_get_duration(self):
        dur = get_wav_duration(self.dummy_wav1)
        self.assertAlmostEqual(dur, self.duration, places=2)

    def test_merge_wavs(self):
        out_wav = self.temp_path / "merged.wav"
        pause_duration = 2.0
        
        # Merge 2 files (each 1s) with a 2s pause in between
        merge_wav_files([self.dummy_wav1, self.dummy_wav2], out_wav, pause_duration=pause_duration)
        
        self.assertTrue(out_wav.exists())
        
        # Total expected duration: 1.0s + 2.0s + 1.0s = 4.0s
        merged_duration = get_wav_duration(out_wav)
        self.assertAlmostEqual(merged_duration, 4.0, places=2)

if __name__ == "__main__":
    unittest.main()
