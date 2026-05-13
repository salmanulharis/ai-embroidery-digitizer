"""
Core image processing module for embroidery digitization
Handles image cleanup, normalization, and preparation
"""

import cv2
import numpy as np
from PIL import Image
from pathlib import Path
from typing import Tuple, Optional
import config
from utils.constants import ProcessingStage


class ImageProcessor:
    """
    Handles all image processing operations
    """
    
    def __init__(self, image_path: str, progress_callback=None):
        """
        Initialize image processor
        
        Args:
            image_path: Path to input image
            progress_callback: Optional callback for progress updates
        """
        self.image_path = Path(image_path)
        self.original_image = None
        self.processed_image = None
        self.progress_callback = progress_callback
        self.width = 0
        self.height = 0
        
        self._load_image()
    
    def _load_image(self):
        """
        Load image from file
        """
        try:
            self.original_image = cv2.imread(str(self.image_path))
            if self.original_image is None:
                raise ValueError("Failed to load image")
            
            # Convert BGR to RGB
            self.original_image = cv2.cvtColor(self.original_image, cv2.COLOR_BGR2RGB)
            self.processed_image = self.original_image.copy()
            self.height, self.width = self.original_image.shape[:2]
            
        except Exception as e:
            raise RuntimeError(f"Error loading image: {str(e)}")
    
    def cleanup_image(self) -> np.ndarray:
        """
        Perform comprehensive image cleanup for embroidery
        Removes noise, reduces small details, smooths edges
        
        Returns:
            Cleaned image
        """
        try:
            img = self.processed_image.copy()
            
            # Convert to grayscale for processing
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            
            # 1. Denoise - bilateral filter preserves edges while removing noise
            denoised = cv2.bilateralFilter(gray, 9, 75, 75)
            
            # 2. Apply morphological operations to remove small objects
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, 
                                              (config.NOISE_REDUCTION_STRENGTH, 
                                               config.NOISE_REDUCTION_STRENGTH))
            
            # Erode to remove small noise
            eroded = cv2.erode(denoised, kernel, iterations=1)
            
            # Dilate to restore main features
            dilated = cv2.dilate(eroded, kernel, iterations=1)
            
            # 3. Apply Gaussian blur for smoothing
            smoothed = cv2.GaussianBlur(dilated, (5, 5), config.EDGE_SMOOTHING_SIGMA)
            
            # 4. Apply threshold to get binary image
            _, binary = cv2.threshold(smoothed, 127, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            
            # 5. Remove small isolated regions
            if config.REMOVE_SMALL_OBJECTS:
                binary = self._remove_small_objects(binary, 
                                                   config.SMALL_OBJECT_SIZE_THRESHOLD)
            
            self.processed_image = binary
            return binary.copy()
            
        except Exception as e:
            raise RuntimeError(f"Error in cleanup_image: {str(e)}")
    
    def _remove_small_objects(self, binary_image: np.ndarray, 
                             min_size: int) -> np.ndarray:
        """
        Remove small isolated objects from binary image
        
        Args:
            binary_image: Binary image
            min_size: Minimum object size in pixels
            
        Returns:
            Cleaned binary image
        """
        try:
            # Find contours
            contours, _ = cv2.findContours(binary_image, cv2.RETR_TREE, 
                                          cv2.CHAIN_APPROX_SIMPLE)
            
            # Create clean image
            clean_image = np.zeros_like(binary_image)
            
            # Draw only large enough contours
            for contour in contours:
                area = cv2.contourArea(contour)
                if area >= min_size:
                    cv2.drawContours(clean_image, [contour], 0, 255, -1)
            
            return clean_image
            
        except Exception as e:
            print(f"Error removing small objects: {e}")
            return binary_image
    
    def resize_image(self, width: int, height: int, 
                    keep_aspect: bool = True) -> np.ndarray:
        """
        Resize image
        
        Args:
            width: Target width in pixels
            height: Target height in pixels
            keep_aspect: Whether to maintain aspect ratio
            
        Returns:
            Resized image
        """
        try:
            if keep_aspect:
                # Calculate scaling to fit within dimensions
                scale = min(width / self.width, height / self.height)
                new_width = int(self.width * scale)
                new_height = int(self.height * scale)
            else:
                new_width = width
                new_height = height
            
            resized = cv2.resize(self.processed_image, (new_width, new_height),
                               interpolation=cv2.INTER_AREA)
            
            self.processed_image = resized
            self.width = new_width
            self.height = new_height
            
            return resized.copy()
            
        except Exception as e:
            raise RuntimeError(f"Error resizing image: {str(e)}")
    
    def apply_threshold(self, threshold_value: int = None) -> np.ndarray:
        """
        Apply thresholding to create binary image
        
        Args:
            threshold_value: Threshold value (0-255). If None, uses Otsu's method
            
        Returns:
            Binary image
        """
        try:
            gray = cv2.cvtColor(self.processed_image, cv2.COLOR_RGB2GRAY)
            
            if threshold_value is None:
                _, binary = cv2.threshold(gray, 0, 255, 
                                         cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            else:
                _, binary = cv2.threshold(gray, threshold_value, 255, cv2.THRESH_BINARY)
            
            self.processed_image = binary
            return binary.copy()
            
        except Exception as e:
            raise RuntimeError(f"Error applying threshold: {str(e)}")
    
    def get_image_stats(self) -> dict:
        """
        Get image statistics
        
        Returns:
            Dictionary of image stats
        """
        try:
            img = self.processed_image
            
            stats = {
                'width': self.width,
                'height': self.height,
                'channels': 3 if len(img.shape) == 3 else 1,
                'dtype': str(img.dtype),
                'size_mb': (img.nbytes / (1024 * 1024)),
                'mean_value': float(np.mean(img)),
                'std_value': float(np.std(img)),
            }
            
            if len(img.shape) == 3:
                stats['channels'] = img.shape[2]
            
            return stats
            
        except Exception as e:
            return {'error': str(e)}
    
    def get_original_image(self) -> np.ndarray:
        """
        Get original image
        
        Returns:
            Original image array
        """
        return self.original_image.copy() if self.original_image is not None else None
    
    def get_processed_image(self) -> np.ndarray:
        """
        Get processed image
        
        Returns:
            Processed image array
        """
        return self.processed_image.copy() if self.processed_image is not None else None
    
    def save_image(self, output_path: str, image: np.ndarray = None) -> bool:
        """
        Save image to file
        
        Args:
            output_path: Path to save image
            image: Image to save (uses processed_image if None)
            
        Returns:
            True if successful
        """
        try:
            if image is None:
                image = self.processed_image
            
            # Convert RGB back to BGR for OpenCV
            if len(image.shape) == 3 and image.shape[2] == 3:
                image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
            
            cv2.imwrite(str(output_path), image)
            return True
            
        except Exception as e:
            print(f"Error saving image: {e}")
            return False
    
    def get_dimensions_mm(self, dpi: int = 96) -> Tuple[float, float]:
        """
        Get image dimensions in millimeters
        
        Args:
            dpi: Dots per inch
            
        Returns:
            Tuple of (width_mm, height_mm)
        """
        from utils.helpers import pixel_to_mm
        width_mm = pixel_to_mm(self.width)
        height_mm = pixel_to_mm(self.height)
        return width_mm, height_mm
