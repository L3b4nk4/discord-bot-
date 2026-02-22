"""
TTS Service - Handles text-to-speech operations.
"""
import discord
import edge_tts
import os
import asyncio
import time
import tempfile


class TTSService:
    """Handles text-to-speech operations using Edge TTS."""
    
    VOICES = {
        'english': 'en-US-ChristopherNeural',
        'english_female': 'en-US-JennyNeural',
        'arabic': 'ar-EG-SalmaNeural',
        'arabic_male': 'ar-EG-ShakirNeural',
    }
    
    def __init__(self):
        self.temp_dir = tempfile.gettempdir()
        self.default_voice = 'english'
    
    def _detect_language(self, text: str) -> str:
        """Detect if text is primarily non-ASCII (Arabic, etc.)."""
        non_ascii = sum(1 for c in text if ord(c) > 127)
        return 'arabic' if non_ascii > len(text) * 0.3 else 'english'
    
    async def speak(self, voice_client: discord.VoiceClient, text: str, 
                    voice_name: str = None) -> bool:
        """
        Convert text to speech and play it.
        
        Args:
            voice_client: Discord voice client
            text: Text to speak
            voice_name: Optional voice name from VOICES dict
            
        Returns:
            True if successful, False otherwise
        """
        if not voice_client or not voice_client.is_connected():
            print("⚠️ TTS: Not connected to voice")
            return False
        
        print(f"🔊 TTS: Speaking '{text[:50]}{'...' if len(text) > 50 else ''}'")
        
        try:
            # Stop any current playback
            if voice_client.is_playing():
                voice_client.stop()
                await asyncio.sleep(0.1)
            
            # Choose voice
            if voice_name and voice_name in self.VOICES:
                voice = self.VOICES[voice_name]
            else:
                lang = self._detect_language(text)
                voice = self.VOICES.get(lang, self.VOICES['english'])
            
            # Generate unique filename
            unique_id = int(time.time() * 1000)
            output_file = os.path.join(self.temp_dir, f"tts_{unique_id}.mp3")
            
            # Generate TTS audio
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(output_file)
            
            # Verify file exists and has content
            if not os.path.exists(output_file) or os.path.getsize(output_file) == 0:
                print("⚠️ TTS: Generated file is empty")
                return False
            
            loop = asyncio.get_running_loop()
            playback_done = asyncio.Event()
            playback_error = {"value": None}

            # Cleanup callback
            def after_playback(error):
                if error:
                    playback_error["value"] = error
                try:
                    if os.path.exists(output_file):
                        os.remove(output_file)
                except Exception as e:
                    print(f"⚠️ TTS Cleanup error: {e}")
                finally:
                    try:
                        loop.call_soon_threadsafe(playback_done.set)
                    except RuntimeError:
                        pass
            
            # Play the audio
            voice_client.play(
                discord.FFmpegPCMAudio(output_file),
                after=after_playback
            )
            
            # Wait for playback to complete (event-driven, no polling loop).
            await playback_done.wait()
            if playback_error["value"]:
                print(f"⚠️ TTS Playback error: {playback_error['value']}")
                return False
            
            return True
            
        except Exception as e:
            print(f"❌ TTS Error: {e}")
            try:
                if 'output_file' in locals() and os.path.exists(output_file):
                    os.remove(output_file)
            except Exception:
                pass
            return False
    
    async def get_available_voices(self) -> list:
        """Get list of available voice names."""
        return list(self.VOICES.keys())
