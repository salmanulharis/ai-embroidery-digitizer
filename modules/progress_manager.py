"""
Progress tracking and management for long-running operations
"""

import threading
from typing import Dict, Callable, Optional
from dataclasses import dataclass, asdict
from utils.constants import ProcessingStage
import time


@dataclass
class ProgressUpdate:
    """Progress update data structure"""
    stage: str
    percentage: int
    message: str
    timestamp: float
    details: Dict = None
    
    def to_dict(self):
        return asdict(self)


class ProgressManager:
    """
    Manages progress tracking for processing operations
    Thread-safe progress manager for embroidery digitization
    """
    
    def __init__(self, session_id: str):
        """
        Initialize progress manager
        
        Args:
            session_id: Unique session identifier
        """
        self.session_id = session_id
        self._lock = threading.Lock()
        self._progress = {
            'stage': ProcessingStage.UPLOADING.value,
            'percentage': 0,
            'message': '',
            'timestamp': time.time(),
            'details': {}
        }
        self._callbacks = []
        self._stages = [
            ProcessingStage.UPLOADING,
            ProcessingStage.VALIDATING,
            ProcessingStage.CLEANING,
            ProcessingStage.BACKGROUND_REMOVAL,
            ProcessingStage.COLOR_DETECTION,
            ProcessingStage.GRADIENT_FLATTENING,
            ProcessingStage.COLOR_REDUCTION,
            ProcessingStage.VECTORIZING,
            ProcessingStage.REGION_MERGING,
            ProcessingStage.STITCH_GENERATION,
            ProcessingStage.PATH_OPTIMIZATION,
            ProcessingStage.TRIM_OPTIMIZATION,
            ProcessingStage.DST_EXPORT,
            ProcessingStage.COMPLETE,
        ]
    
    def register_callback(self, callback: Callable):
        """
        Register callback to be called on progress updates
        
        Args:
            callback: Callable that receives ProgressUpdate
        """
        with self._lock:
            self._callbacks.append(callback)
    
    def update(self, stage: ProcessingStage, percentage: int = None, 
               message: str = None, details: Dict = None):
        """
        Update progress
        
        Args:
            stage: Current processing stage
            percentage: Progress percentage (0-100), auto-calculated if None
            message: Human-readable status message
            details: Additional details dict
        """
        with self._lock:
            # Auto-calculate percentage based on stage
            if percentage is None:
                stage_index = next((i for i, s in enumerate(self._stages) 
                                  if s == stage), 0)
                percentage = int((stage_index / len(self._stages)) * 100)
            
            self._progress['stage'] = stage.value
            self._progress['percentage'] = max(0, min(100, percentage))
            self._progress['message'] = message or f"Processing: {stage.value}"
            self._progress['timestamp'] = time.time()
            
            if details:
                self._progress['details'].update(details)
        
        # Trigger callbacks
        self._trigger_callbacks()
    
    def get_progress(self) -> Dict:
        """
        Get current progress status
        
        Returns:
            Progress dictionary
        """
        with self._lock:
            return self._progress.copy()
    
    def set_percentage(self, percentage: int):
        """
        Directly set progress percentage
        
        Args:
            percentage: Progress percentage (0-100)
        """
        with self._lock:
            self._progress['percentage'] = max(0, min(100, percentage))
            self._progress['timestamp'] = time.time()
        
        self._trigger_callbacks()
    
    def set_message(self, message: str):
        """
        Update progress message
        
        Args:
            message: Status message
        """
        with self._lock:
            self._progress['message'] = message
            self._progress['timestamp'] = time.time()
        
        self._trigger_callbacks()
    
    def set_details(self, details: Dict):
        """
        Update progress details
        
        Args:
            details: Details dictionary
        """
        with self._lock:
            self._progress['details'].update(details)
            self._progress['timestamp'] = time.time()
        
        self._trigger_callbacks()
    
    def _trigger_callbacks(self):
        """
        Trigger all registered callbacks
        """
        with self._lock:
            callbacks = self._callbacks.copy()
        
        progress = self.get_progress()
        for callback in callbacks:
            try:
                callback(progress)
            except Exception as e:
                print(f"Error in progress callback: {e}")
    
    def complete(self, message: str = "Processing complete"):
        """
        Mark processing as complete
        
        Args:
            message: Completion message
        """
        self.update(ProcessingStage.COMPLETE, 100, message)
    
    def error(self, error_message: str):
        """
        Mark processing with error
        
        Args:
            error_message: Error message
        """
        with self._lock:
            self._progress['stage'] = 'error'
            self._progress['percentage'] = 0
            self._progress['message'] = f"Error: {error_message}"
            self._progress['timestamp'] = time.time()
        
        self._trigger_callbacks()


# Global progress managers storage
_progress_managers: Dict[str, ProgressManager] = {}
_managers_lock = threading.Lock()


def get_progress_manager(session_id: str) -> ProgressManager:
    """
    Get or create progress manager for session
    
    Args:
        session_id: Session identifier
        
    Returns:
        ProgressManager instance
    """
    global _progress_managers
    
    with _managers_lock:
        if session_id not in _progress_managers:
            _progress_managers[session_id] = ProgressManager(session_id)
        return _progress_managers[session_id]


def cleanup_progress_manager(session_id: str):
    """
    Clean up progress manager for session
    
    Args:
        session_id: Session identifier
    """
    global _progress_managers
    
    with _managers_lock:
        if session_id in _progress_managers:
            del _progress_managers[session_id]
