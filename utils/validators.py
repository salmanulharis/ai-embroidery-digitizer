"""
Validation utilities for file uploads and parameters
"""

import os
from pathlib import Path
from typing import Tuple, Optional
import mimetypes
from PIL import Image
import config
from utils.constants import ERROR_MESSAGES


class ValidationError(Exception):
    """Custom validation error"""
    pass


def validate_file_upload(file) -> Tuple[bool, Optional[str]]:
    """
    Validate uploaded file for embroidery digitization
    
    Args:
        file: Werkzeug FileStorage object
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        # Check if file exists
        if not file or file.filename == '':
            return False, ERROR_MESSAGES['no_file_provided']
        
        # Get file extension
        filename = file.filename
        ext = Path(filename).suffix.lower().lstrip('.')
        
        # Check file extension
        if ext not in config.ALLOWED_EXTENSIONS:
            return False, ERROR_MESSAGES['invalid_file_type']
        
        # Save temp file to check MIME type
        temp_path = Path(config.UPLOAD_FOLDER) / f"temp_{filename}"
        file.save(str(temp_path))
        
        # Check file size
        file_size = os.path.getsize(temp_path)
        if file_size > config.MAX_CONTENT_LENGTH:
            temp_path.unlink()
            return False, ERROR_MESSAGES['file_too_large']
        
        # Verify it's a valid image
        try:
            img = Image.open(temp_path)
            img.verify()
            img = Image.open(temp_path)  # Need to reopen after verify
            
            width, height = img.size
            
            # Check minimum image size
            if width < 10 or height < 10:
                temp_path.unlink()
                return False, ERROR_MESSAGES['file_too_small']
            
            # Check MIME type
            mime_type, _ = mimetypes.guess_type(str(temp_path))
            if mime_type not in config.ALLOWED_MIMETYPES:
                temp_path.unlink()
                return False, ERROR_MESSAGES['invalid_file_type']
            
        except Exception as e:
            temp_path.unlink()
            return False, f"Invalid image file: {str(e)}"
        
        # Clean up temp file
        temp_path.unlink()
        return True, None
        
    except Exception as e:
        return False, ERROR_MESSAGES['server_error']


def validate_dimensions(width: float, height: float) -> Tuple[bool, Optional[str]]:
    """
    Validate output dimensions for embroidery
    
    Args:
        width: Width in millimeters
        height: Height in millimeters
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        if not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
            return False, ERROR_MESSAGES['invalid_dimensions']
        
        if width <= 0 or height <= 0:
            return False, ERROR_MESSAGES['invalid_dimensions']
        
        if width < config.MIN_IMAGE_SIZE_MM['width'] or width > config.MAX_IMAGE_SIZE_MM['width']:
            return False, f"Width must be between {config.MIN_IMAGE_SIZE_MM['width']} and {config.MAX_IMAGE_SIZE_MM['width']}mm"
        
        if height < config.MIN_IMAGE_SIZE_MM['height'] or height > config.MAX_IMAGE_SIZE_MM['height']:
            return False, f"Height must be between {config.MIN_IMAGE_SIZE_MM['height']} and {config.MAX_IMAGE_SIZE_MM['height']}mm"
        
        return True, None
        
    except Exception as e:
        return False, ERROR_MESSAGES['server_error']


def validate_color_count(num_colors: int) -> Tuple[bool, Optional[str]]:
    """
    Validate number of output colors
    
    Args:
        num_colors: Number of colors
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        if not isinstance(num_colors, int):
            return False, ERROR_MESSAGES['invalid_color_count']
        
        if num_colors < config.MIN_NUM_COLORS or num_colors > config.MAX_NUM_COLORS:
            return False, f"Number of colors must be between {config.MIN_NUM_COLORS} and {config.MAX_NUM_COLORS}"
        
        return True, None
        
    except Exception as e:
        return False, ERROR_MESSAGES['server_error']


def validate_stitch_density(density: float) -> Tuple[bool, Optional[str]]:
    """
    Validate stitch density (stitches per mm)
    
    Args:
        density: Stitch density value
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        if not isinstance(density, (int, float)):
            return False, "Invalid stitch density"
        
        if density < 1 or density > 20:
            return False, "Stitch density must be between 1 and 20 stitches per mm"
        
        return True, None
        
    except Exception as e:
        return False, ERROR_MESSAGES['server_error']


def validate_processing_parameters(params: dict) -> Tuple[bool, Optional[str]]:
    """
    Validate all processing parameters
    
    Args:
        params: Dictionary of processing parameters
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        # Validate dimensions
        if 'width' in params and 'height' in params:
            is_valid, error = validate_dimensions(params['width'], params['height'])
            if not is_valid:
                return False, error
        
        # Validate color count
        if 'num_colors' in params:
            is_valid, error = validate_color_count(params['num_colors'])
            if not is_valid:
                return False, error
        
        # Validate stitch density
        if 'stitch_density' in params:
            is_valid, error = validate_stitch_density(params['stitch_density'])
            if not is_valid:
                return False, error
        
        return True, None
        
    except Exception as e:
        return False, ERROR_MESSAGES['server_error']


def is_allowed_file(filename: str) -> bool:
    """
    Check if file extension is allowed
    
    Args:
        filename: Filename to check
        
    Returns:
        True if allowed, False otherwise
    """
    return '.' in filename and Path(filename).suffix.lower().lstrip('.') in config.ALLOWED_EXTENSIONS
