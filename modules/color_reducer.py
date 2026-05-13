"""
Color reduction and quantization module
Reduces image colors to embroidery-friendly thread colors
"""

import numpy as np
from typing import List, Tuple, Optional
from sklearn.cluster import KMeans
import cv2
from skimage import color
import config
from utils.helpers import calculate_color_distance, rgb_to_lab, lab_to_rgb


class ColorReducer:
    """
    Reduces image to limited color palette
    Quantizes colors intelligently for embroidery
    """
    
    def __init__(self, image: np.ndarray, num_colors: int = None):
        """
        Initialize color reducer
        
        Args:
            image: Input image (RGB format)
            num_colors: Number of output colors
        """
        self.original_image = image.copy()
        self.num_colors = num_colors or config.DEFAULT_NUM_COLORS
        self.quantized_image = None
        self.palette = None
        self.color_map = {}  # Maps original colors to quantized colors
    
    def reduce_colors_kmeans(self) -> Tuple[np.ndarray, List[Tuple]]:
        """
        Reduce colors using KMeans clustering
        
        Returns:
            Tuple of (quantized_image, palette)
        """
        try:
            # Reshape image to 2D array of pixels
            img = self.original_image
            pixels = img.reshape(-1, 3).astype(np.float32)
            
            # Apply KMeans clustering
            kmeans = KMeans(n_clusters=self.num_colors, random_state=42, n_init=10)
            kmeans.fit(pixels)
            
            # Get cluster centers (palette)
            palette = kmeans.cluster_centers_.astype(np.uint8)
            
            # Get labels for each pixel
            labels = kmeans.labels_
            
            # Create quantized image
            quantized = palette[labels].reshape(img.shape).astype(np.uint8)
            
            self.quantized_image = quantized
            self.palette = palette
            
            return quantized, [tuple(c) for c in palette]
            
        except Exception as e:
            raise RuntimeError(f"Error in KMeans color reduction: {str(e)}")
    
    def reduce_colors_median_cut(self) -> Tuple[np.ndarray, List[Tuple]]:
        """
        Reduce colors using Median Cut algorithm
        
        Returns:
            Tuple of (quantized_image, palette)
        """
        try:
            from PIL import Image as PILImage
            
            # Convert to PIL Image
            pil_img = PILImage.fromarray(self.original_image)
            
            # Quantize using PIL
            quantized_pil = pil_img.quantize(colors=self.num_colors, method=2)  # method=2 is median cut
            
            # Convert back to numpy
            quantized = np.array(quantized_pil.convert('RGB'))
            palette = list(quantized_pil.palette.getdata()[1])
            palette = [tuple(palette[i:i+3]) for i in range(0, len(palette), 3)]
            
            self.quantized_image = quantized
            self.palette = palette
            
            return quantized, palette[:self.num_colors]
            
        except Exception as e:
            raise RuntimeError(f"Error in Median Cut color reduction: {str(e)}")
    
    def reduce_colors(self, method: str = None) -> Tuple[np.ndarray, List[Tuple]]:
        """
        Reduce colors using specified method
        
        Args:
            method: Quantization method ('kmeans' or 'median_cut')
            
        Returns:
            Tuple of (quantized_image, palette)
        """
        method = method or config.COLOR_QUANTIZATION_METHOD
        
        if method == 'kmeans':
            return self.reduce_colors_kmeans()
        elif method == 'median_cut':
            return self.reduce_colors_median_cut()
        else:
            return self.reduce_colors_kmeans()  # Default to KMeans
    
    def merge_similar_colors(self, threshold: float = None) -> Tuple[np.ndarray, List[Tuple]]:
        """
        Merge similar colors in palette to reduce final color count
        
        Args:
            threshold: LAB color distance threshold for merging
            
        Returns:
            Tuple of (merged_quantized_image, merged_palette)
        """
        try:
            threshold = threshold or config.COLOR_MERGE_THRESHOLD
            
            if self.palette is None:
                self.reduce_colors()
            
            # Convert palette to LAB for better perceptual distance
            palette_rgb = np.array(self.palette).reshape(1, -1, 3).astype(np.float32) / 255.0
            palette_lab = color.rgb2lab(palette_rgb)[0]
            
            # Find colors to merge
            merged_palette = []
            merged_map = {}  # Maps old indices to new indices
            
            for i, color_lab in enumerate(palette_lab):
                # Check if similar to any existing merged color
                found_match = False
                for j, merged_color in enumerate(merged_palette):
                    dist = calculate_color_distance(color_lab, merged_color, space='lab')
                    if dist < threshold:
                        merged_map[i] = j
                        found_match = True
                        break
                
                if not found_match:
                    merged_palette.append(color_lab)
                    merged_map[i] = len(merged_palette) - 1
            
            # Convert merged palette back to RGB
            merged_palette = np.array(merged_palette).reshape(-1, 1, 3)
            merged_palette = (color.lab2rgb(merged_palette) * 255).astype(np.uint8)[0]
            
            # Remap quantized image
            quantized_remapped = np.zeros_like(self.quantized_image)
            for i, old_idx in enumerate(range(len(self.palette))):
                mask = self.quantized_image == self.palette[old_idx]
                new_idx = merged_map[old_idx]
                quantized_remapped[mask] = merged_palette[new_idx]
            
            self.quantized_image = quantized_remapped
            self.palette = merged_palette
            
            return quantized_remapped, [tuple(c) for c in merged_palette]
            
        except Exception as e:
            print(f"Error merging colors: {e}")
            return self.quantized_image, [tuple(c) for c in self.palette]
    
    def get_quantized_image(self) -> np.ndarray:
        """
        Get quantized image
        
        Returns:
            Quantized image
        """
        if self.quantized_image is None:
            self.reduce_colors()
        return self.quantized_image.copy()
    
    def get_palette(self) -> List[Tuple]:
        """
        Get color palette
        
        Returns:
            List of RGB color tuples
        """
        if self.palette is None:
            self.reduce_colors()
        return [tuple(c) for c in self.palette]
    
    def set_num_colors(self, num_colors: int):
        """
        Change number of output colors
        
        Args:
            num_colors: New number of colors
        """
        self.num_colors = max(1, min(num_colors, config.MAX_NUM_COLORS))
        self.quantized_image = None  # Reset quantized image
        self.palette = None
