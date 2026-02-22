"""
Voice Sink - Custom audio receiver for Discord voice.
"""
from discord.ext import voice_recv
import struct
import math
import time
import os


class VoiceSink(voice_recv.AudioSink):
    """
    Receives and buffers voice audio from Discord users.
    Processes audio when silence is detected.
    """
    
    # Configuration
    MIN_AUDIO_LENGTH = 48000    # Minimum bytes (~0.5 sec at 48kHz stereo)
    MAX_AUDIO_LENGTH = 960000   # Maximum bytes (~5 sec) to prevent memory issues
    SILENCE_TIMEOUT = 1.0       # Seconds of silence before processing
    MIN_RMS = 150               # Minimum volume threshold
    
    def __init__(self, voice_handler):
        """
        Initialize the voice sink.
        
        Args:
            voice_handler: Reference to the VoiceHandler instance
        """
        super().__init__()
        self.voice_handler = voice_handler
        self.verbose_logs = os.getenv("VOICE_VERBOSE_LOGS", "0").lower() in {"1", "true", "yes", "on"}
        
        # User audio buffers: user_id -> bytearray
        self.user_buffers = {}
        # Last audio timestamp per user: user_id -> float
        self.last_audio_time = {}
        # Users currently being processed
        self.processing = set()
        # Debug counter
        self.packet_count = 0
    
    def wants_opus(self) -> bool:
        """We want decoded PCM audio, not raw Opus packets."""
        return False
    
    def write(self, user, data):
        """
        Called when audio data is received from a user.
        
        Args:
            user: The Discord user who sent the audio
            data: VoiceData object containing PCM audio
        """
        # Skip if listening is disabled
        if not self.voice_handler.listening:
            return
        
        # Skip if owner-only mode and not owner
        if self.voice_handler.owner_only and user:
            if user.id != self.voice_handler.owner_id:
                return

        # Skip if allow-list mode is active and user isn't allowed
        if (not self.voice_handler.owner_only) and self.voice_handler.allowed_users:
            if not user or user.id not in self.voice_handler.allowed_users:
                return
        
        # Skip blocked users
        if user and user.id in self.voice_handler.blocked_users:
            return
        
        # Get user ID (0 for unknown users)
        uid = user.id if user else 0
        
        # Skip if no audio data
        if not data.pcm:
            return
        
        # Initialize buffer for new user
        if uid not in self.user_buffers:
            self.user_buffers[uid] = bytearray()
            if self.verbose_logs:
                username = user.display_name if user else "Unknown"
                print(f"🎤 Started receiving audio from {username}")
        
        # Check buffer size limit
        current_size = len(self.user_buffers[uid])
        if current_size > self.MAX_AUDIO_LENGTH:
            # Buffer too large, process what we have
            self.last_audio_time[uid] = 0  # Force processing
            return
        
        # Append PCM data to buffer
        self.user_buffers[uid].extend(data.pcm)
        self.last_audio_time[uid] = time.time()
        
        # Debug logging
        self.packet_count += 1
        if self.verbose_logs and self.packet_count % 500 == 0:
            print(f"📊 Received {self.packet_count} audio packets")
    
    def cleanup(self):
        """Called when the sink is being cleaned up."""
        self.user_buffers.clear()
        self.last_audio_time.clear()
        self.processing.clear()
        print("🧹 Voice sink cleaned up")
    
    def get_ready_segments(self) -> list:
        """
        Get audio segments that are ready for processing.
        A segment is ready when there's been silence for SILENCE_TIMEOUT.
        
        Returns:
            List of tuples: (user_id, audio_bytes)
        """
        ready = []
        now = time.time()
        
        for uid in list(self.user_buffers.keys()):
            # Skip if already being processed
            if uid in self.processing:
                continue
            
            buffer = self.user_buffers.get(uid, bytearray())
            last_time = self.last_audio_time.get(uid, now)
            
            # Check if silence timeout has passed and we have enough data
            silence_time = now - last_time
            has_enough_data = len(buffer) > self.MIN_AUDIO_LENGTH
            silence_detected = silence_time > self.SILENCE_TIMEOUT
            
            if has_enough_data and silence_detected:
                # Extract audio and clear buffer
                audio_data = bytes(buffer)
                self.user_buffers[uid] = bytearray()
                self.processing.add(uid)
                ready.append((uid, audio_data))
                if self.verbose_logs:
                    print(f"🎙️ Segment ready: {len(audio_data)} bytes, silence: {silence_time:.1f}s")
        
        return ready
    
    def finish_processing(self, user_id: int):
        """Mark a user as done processing."""
        self.processing.discard(user_id)
    
    @staticmethod
    def calculate_rms(audio_data: bytes) -> float:
        """
        Calculate Root Mean Square (volume level) of audio.
        
        Args:
            audio_data: Raw PCM audio bytes (16-bit)
            
        Returns:
            RMS value (higher = louder)
        """
        count = len(audio_data) // 2
        if count == 0:
            return 0.0
        
        try:
            # Unpack as 16-bit signed integers
            shorts = struct.unpack(f"<{count}h", audio_data[:count * 2])
            # Calculate RMS
            sum_squares = sum(s ** 2 for s in shorts)
            return math.sqrt(sum_squares / count)
        except Exception:
            return 0.0
    
    @staticmethod
    def is_loud_enough(audio_data: bytes, threshold: float = None) -> bool:
        """
        Check if audio is loud enough to process.
        
        Args:
            audio_data: Raw PCM audio bytes
            threshold: RMS threshold (uses MIN_RMS if not specified)
            
        Returns:
            True if audio is above threshold
        """
        if threshold is None:
            threshold = VoiceSink.MIN_RMS
        
        rms = VoiceSink.calculate_rms(audio_data)
        return rms >= threshold
