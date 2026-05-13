"""
Gradient detection and flattening module
Detects and converts gradients into flat embroidery-friendly regions
"""

import numpy as np
from typing import List, Dict, Tuple
from scipy import ndimage
import cv2
import config
from utils.helpers import calculate_color_distance, rgb_to_lab
from skimage import color


class GradientHandler:
    """
    Detects and flattens gradients in images
    """
    
    def __init__(self, image: np.ndarray):
        """
        Initialize gradient handler
        
        Args:
            image: Input image (RGB)
        """
        self.original_image = image.copy()
        self.gradient_mask = None
        self.flattened_image = None
    
    def detect_gradients(self) -> np.ndarray:
        """
        Detect gradient regions in image
        
        Returns:
            Binary mask of gradient regions
        """
        try:
            # Convert to LAB color space for better gradient detection
            img_float = self.original_image.astype(np.float32) / 255.0
            img_lab = color.rgb2lab(img_float)
            
            # Calculate color variance in local regions
            kernel_size = config.GRADIENT_REGION_SIZE
            variance_map = self._calculate_local_variance(img_lab, kernel_size)
            
            # Threshold to identify gradient regions
            gradient_threshold = np.mean(variance_map) + 0.5 * np.std(variance_map)
            gradient_mask = variance_map > gradient_threshold
            
            self.gradient_mask = gradient_mask.astype(np.uint8) * 255
            return self.gradient_mask
            
        except Exception as e:
            print(f"Error detecting gradients: {e}")
            return np.zeros(self.original_image.shape[:2], dtype=np.uint8)
    
    def _calculate_local_variance(self, img_lab: np.ndarray, kernel_size: int) -> np.ndarray:
        """
        Calculate local color variance
        
        Args:
            img_lab: Image in LAB color space
            kernel_size: Size of local region
            
        Returns:
            Variance map
        """
        variance_map = np.zeros((img_lab.shape[0], img_lab.shape[1]))
        half_size = kernel_size // 2
        
        for i in range(half_size, img_lab.shape[0] - half_size):
            for j in range(half_size, img_lab.shape[1] - half_size):
                region = img_lab[i-half_size:i+half_size, j-half_size:j+half_size]
                # Calculate variance of L channel
                variance_map[i, j] = np.var(region[:, :, 0])
        
        return variance_map
    
    def flatten_gradients(self, num_levels: int = None) -> np.ndarray:
        """
        Flatten gradients into discrete color regions
        
        Args:
            num_levels: Number of discrete color levels
            
        Returns:
            Flattened image
        """
        try:
            num_levels = num_levels or config.DEFAULT_NUM_COLORS
            
            # Convert to LAB for better color space handling
            img_float = self.original_image.astype(np.float32) / 255.0
            img_lab = color.rgb2lab(img_float)
            
            # Process each channel
            flattened_lab = img_lab.copy()
            
            for c in range(3):
                channel = img_lab[:, :, c]
                
                # Quantize to discrete levels
                min_val = np.min(channel)
                max_val = np.max(channel)
                
                # Create bins
                quantized = np.digitize(channel, np.linspace(min_val, max_val, num_levels))
                
                # Map back to continuous values (center of each bin)
                bin_centers = np.linspace(min_val, max_val, num_levels)
                flattened_lab[:, :, c] = bin_centers[quantized - 1]
            
            # Merge nearby similar colors
            flattened_lab = self._merge_nearby_colors(flattened_lab)
            
            # Convert back to RGB
            flattened_rgb = color.lab2rgb(flattened_lab)
            flattened_rgb = (flattened_rgb * 255).astype(np.uint8)
            
            self.flattened_image = flattened_rgb
            return flattened_rgb
            
        except Exception as e:
            print(f"Error flattening gradients: {e}")
            return self.original_image.copy()
    
    def _merge_nearby_colors(self, img_lab: np.ndarray) -> np.ndarray:
        """
        Merge nearby similar colors
        
        Args:
            img_lab: Image in LAB color space
            
        Returns:
            Image with merged colors
        """
        try:
            h, w = img_lab.shape[:2]
            merged = img_lab.copy()
            
            # Simplified color merging using morphological operations
            for c in range(3):
                channel = merged[:, :, c].astype(np.uint8)
                
                # Apply bilateral filter to smooth similar colors
                smoothed = cv2.bilateralFilter(channel, 9, 75, 75)
                merged[:, :, c] = smoothed.astype(np.float32) * (np.max(channel) / 255.0)
            
            return merged
            
        except Exception as e:
            print(f"Error merging colors: {e}")
            return img_lab
    
    def segment_into_regions(self, num_regions: int = None) -> Dict:
        """
        Segment image into embroidery-friendly regions
        
        Args:
            num_regions: Number of regions to create
            
        Returns:
            Dictionary with region masks and colors
        """
        try:
            num_regions = num_regions or config.DEFAULT_NUM_COLORS
            
            # Flatten gradients first
            if self.flattened_image is None:
                self.flatten_gradients(num_regions)
            
            # Use K-means clustering to segment into regions
            from sklearn.cluster import KMeans
            
            pixels = self.flattened_image.reshape(-1, 3).astype(np.float32)
            kmeans = KMeans(n_clusters=num_regions, random_state=42, n_init=10)
            labels = kmeans.fit_predict(pixels)
            
            # Reshape labels back to image dimensions
            label_image = labels.reshape(self.flattened_image.shape[:2])
            
            # Create region dictionary
            regions = {}
            for i in range(num_regions):
                region_mask = (label_image == i)
                color = kmeans.cluster_centers_[i].astype(np.uint8)
                
                if np.sum(region_mask) > config.MIN_REGION_SIZE:
                    regions[i] = {
                        'mask': region_mask,
                        'color': tuple(color),
                        'size': np.sum(region_mask),
                    }
            
            return regions
            
        except Exception as e:
            print(f"Error segmenting into regions: {e}")
            return {}
    
    def get_flattened_image(self) -> np.ndarray:
        """
        Get flattened image
        
        Returns:
            Flattened image
        """
        if self.flattened_image is None:
            self.flatten_gradients()
        return self.flattened_image.copy()
