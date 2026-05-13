"""
Helper functions and utilities
"""

import hashlib
from pathlib import Path
from datetime import datetime
import json
from typing import Dict, Any, Tuple
import math
import numpy as np
from utils.constants import PIXEL_TO_MM, MM_TO_PIXEL


def generate_session_id(filename: str, timestamp: float = None) -> str:
    """
    Generate unique session ID for processing
    
    Args:
        filename: Original filename
        timestamp: Optional timestamp
        
    Returns:
        Unique session ID
    """
    if timestamp is None:
        timestamp = datetime.now().timestamp()
    
    hash_input = f"{filename}{timestamp}"
    return hashlib.sha256(hash_input.encode()).hexdigest()[:16]


def get_session_folder(session_id: str) -> Path:
    """
    Get folder path for session data
    
    Args:
        session_id: Session ID
        
    Returns:
        Path to session folder
    """
    from config import OUTPUT_FOLDER
    session_folder = OUTPUT_FOLDER / session_id
    session_folder.mkdir(parents=True, exist_ok=True)
    return session_folder


def save_session_state(session_id: str, state: Dict[str, Any]) -> bool:
    """
    Save session state to JSON file
    
    Args:
        session_id: Session ID
        state: State dictionary to save
        
    Returns:
        True if successful
    """
    try:
        session_folder = get_session_folder(session_id)
        state_file = session_folder / 'state.json'
        
        with open(state_file, 'w') as f:
            json.dump(state, f, indent=2)
        
        return True
    except Exception as e:
        print(f"Error saving session state: {e}")
        return False


def load_session_state(session_id: str) -> Dict[str, Any]:
    """
    Load session state from JSON file
    
    Args:
        session_id: Session ID
        
    Returns:
        State dictionary or empty dict if not found
    """
    try:
        session_folder = get_session_folder(session_id)
        state_file = session_folder / 'state.json'
        
        if state_file.exists():
            with open(state_file, 'r') as f:
                return json.load(f)
        
        return {}
    except Exception as e:
        print(f"Error loading session state: {e}")
        return {}


def pixel_to_mm(pixels: float) -> float:
    """
    Convert pixels to millimeters (at 96 DPI)
    
    Args:
        pixels: Value in pixels
        
    Returns:
        Value in millimeters
    """
    return pixels * PIXEL_TO_MM


def mm_to_pixel(mm: float) -> float:
    """
    Convert millimeters to pixels (at 96 DPI)
    
    Args:
        mm: Value in millimeters
        
    Returns:
        Value in pixels
    """
    return mm * MM_TO_PIXEL


def rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
    """
    Convert RGB tuple to hex color string
    
    Args:
        rgb: Tuple of (R, G, B) values (0-255)
        
    Returns:
        Hex color string (#RRGGBB)
    """
    return '#{:02x}{:02x}{:02x}'.format(int(rgb[0]), int(rgb[1]), int(rgb[2]))


def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    """
    Convert hex color string to RGB tuple
    
    Args:
        hex_color: Hex color string (#RRGGBB)
        
    Returns:
        Tuple of (R, G, B) values (0-255)
    """
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """
    Convert RGB to LAB color space
    
    Args:
        rgb: RGB array with values 0-255
        
    Returns:
        LAB array
    """
    from skimage.color import rgb2lab
    
    # Normalize to 0-1
    rgb_normalized = rgb.astype(np.float32) / 255.0
    return rgb2lab(rgb_normalized)


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    """
    Convert LAB to RGB color space
    
    Args:
        lab: LAB array
        
    Returns:
        RGB array with values 0-255
    """
    from skimage.color import lab2rgb
    
    rgb_normalized = lab2rgb(lab)
    return (rgb_normalized * 255).astype(np.uint8)


def calculate_color_distance(color1: np.ndarray, color2: np.ndarray, space: str = 'lab') -> float:
    """
    Calculate distance between two colors
    
    Args:
        color1: First color
        color2: Second color
        space: Color space ('lab' or 'rgb')
        
    Returns:
        Distance value
    """
    if space == 'lab':
        return np.sqrt(np.sum((color1 - color2) ** 2))
    elif space == 'rgb':
        # Euclidean distance in RGB space (weighted for human perception)
        r_diff = color1[0] - color2[0]
        g_diff = color1[1] - color2[1]
        b_diff = color1[2] - color2[2]
        return math.sqrt(0.299 * r_diff**2 + 0.587 * g_diff**2 + 0.114 * b_diff**2)
    else:
        return float('inf')


def estimate_embroidery_time(stitch_count: int, machine_speed_spm: int = 1000) -> float:
    """
    Estimate embroidery time in minutes
    
    Args:
        stitch_count: Total number of stitches
        machine_speed_spm: Machine speed in stitches per minute (default 1000 spm)
        
    Returns:
        Estimated time in minutes
    """
    return stitch_count / machine_speed_spm


def estimate_thread_changes(num_colors: int, color_regions: int) -> int:
    """
    Estimate number of thread changes needed
    
    Args:
        num_colors: Number of colors
        color_regions: Number of separate color regions
        
    Returns:
        Estimated thread changes
    """
    # At least num_colors-1 changes, potentially more if colors are scattered
    return max(num_colors - 1, min(color_regions - 1, num_colors + 5))


def get_file_size_mb(file_path: Path) -> float:
    """
    Get file size in megabytes
    
    Args:
        file_path: Path to file
        
    Returns:
        File size in MB
    """
    if file_path.exists():
        return file_path.stat().st_size / (1024 * 1024)
    return 0


def cleanup_old_files(folder: Path, max_age_hours: int = 24) -> int:
    """
    Clean up old files in a folder
    
    Args:
        folder: Folder path
        max_age_hours: Maximum age in hours
        
    Returns:
        Number of files deleted
    """
    from datetime import datetime, timedelta
    import os
    
    deleted_count = 0
    now = datetime.now()
    
    try:
        for item in folder.glob('*'):
            if item.is_file():
                file_age = now - datetime.fromtimestamp(item.stat().st_mtime)
                if file_age > timedelta(hours=max_age_hours):
                    item.unlink()
                    deleted_count += 1
    except Exception as e:
        print(f"Error cleaning up files: {e}")
    
    return deleted_count


def format_stitch_count(count: int) -> str:
    """
    Format stitch count with thousands separator
    
    Args:
        count: Stitch count
        
    Returns:
        Formatted string
    """
    return f"{count:,}"


def format_time(minutes: float) -> str:
    """
    Format time in minutes to readable string
    
    Args:
        minutes: Time in minutes
        
    Returns:
        Formatted time string
    """
    if minutes < 1:
        return f"{int(minutes * 60)}s"
    elif minutes < 60:
        return f"{minutes:.1f}m"
    else:
        hours = int(minutes // 60)
        mins = int(minutes % 60)
        return f"{hours}h {mins}m"
