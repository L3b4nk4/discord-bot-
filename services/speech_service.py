"""
Speech Recognition Service - Handles speech-to-text operations.
"""
import speech_recognition as sr
import asyncio
import wave
import os
import time
import tempfile


class SpeechRecognitionService:
    """Handles speech-to-text operations using Google Speech Recognition."""
    
    SUPPORTED_LANGUAGES = {
        'en': 'en-US',
        'ar': 'ar-EG',
        'es': 'es-ES',
        'fr': 'fr-FR',
        'de': 'de-DE',
    }
    
    def __init__(self):
        self.recognizer = sr.Recognizer()
        # Adjust for ambient noise sensitivity
        self.recognizer.energy_threshold = 300
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.pause_threshold = 0.8
        self.temp_dir = tempfile.gettempdir()
        self.default_language = 'en-US'
    
    async def transcribe(self, audio_data: bytes, sample_rate: int = 48000,
                         channels: int = 2, language: str = None) -> str:
        """
        Transcribe audio data to text.
        
        Args:
            audio_data: Raw PCM audio bytes
            sample_rate: Audio sample rate (Discord uses 48000)
            channels: Number of audio channels (Discord uses 2 for stereo)
            language: Language code for recognition
            
        Returns:
            Transcribed text or empty string if failed
        """
        if not audio_data or len(audio_data) < 1000:
            return ""
        
        # Use default language if not specified
        lang = language or self.default_language
        
        try:
            # Create unique temporary filename
            unique_id = int(time.time() * 1000)
            wav_path = os.path.join(self.temp_dir, f"speech_{unique_id}.wav")
            
            # Save as WAV file
            await self._save_as_wav(audio_data, wav_path, sample_rate, channels)
            
            # Verify file exists
            if not os.path.exists(wav_path):
                print("❌ Speech: Failed to create WAV file")
                return ""
            
            # Load and transcribe
            with sr.AudioFile(wav_path) as source:
                # Adjust for noise
                self.recognizer.adjust_for_ambient_noise(source, duration=0.2)
                audio = self.recognizer.record(source)
            
            # Perform recognition in thread pool
            text = await asyncio.to_thread(
                self.recognizer.recognize_google,
                audio,
                language=lang
            )
            
            # Cleanup
            self._cleanup_file(wav_path)
            
            return text.strip()
            
        except sr.UnknownValueError:
            # Could not understand audio - this is normal
            return ""
        except sr.RequestError as e:
            print(f"❌ Speech Recognition API Error: {e}")
            return ""
        except Exception as e:
            print(f"❌ Speech Recognition Error: {e}")
            return ""
    
    async def _save_as_wav(self, audio_data: bytes, filepath: str,
                          sample_rate: int, channels: int):
        """Save raw PCM audio data as WAV file."""
        def _write_wav():
            with wave.open(filepath, 'wb') as wf:
                wf.setnchannels(channels)
                wf.setsampwidth(2)  # 16-bit audio
                wf.setframerate(sample_rate)
                wf.writeframes(audio_data)
        
        await asyncio.to_thread(_write_wav)
    
    def _cleanup_file(self, filepath: str):
        """Safely remove a temporary file."""
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception as e:
            print(f"⚠️ Speech: Cleanup error - {e}")
    
    def set_language(self, language_code: str) -> bool:
        """
        Set the default recognition language.
        
        Args:
            language_code: Short language code (e.g., 'en', 'ar')
            
        Returns:
            True if language was set, False if not supported
        """
        if language_code in self.SUPPORTED_LANGUAGES:
            self.default_language = self.SUPPORTED_LANGUAGES[language_code]
            return True
        return False
