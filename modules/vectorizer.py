"""
Vectorization module - converts raster to vector paths
Generates contours and vectorizes image regions
"""

import cv2
import numpy as np
from typing import List, Tuple, Dict
from pathlib import Path
import config
from utils.helpers import pixel_to_mm


class Vectorizer:
    """
    Vectorizes images into contours and paths
    """
    
    def __init__(self, binary_image: np.ndarray):
        """
        Initialize vectorizer
        
        Args:
            binary_image: Binary (grayscale) image
        """
        self.binary_image = binary_image.copy()
        self.contours = None
        self.hierarchy = None
        self.simplified_contours = None
    
    def extract_contours(self, simplify: bool = True) -> List[np.ndarray]:
        """
        Extract contours from binary image
        
        Args:
            simplify: Whether to simplify contours
            
        Returns:
            List of contours
        """
        try:
            # Find contours
            contours, hierarchy = cv2.findContours(self.binary_image, 
                                                   cv2.RETR_TREE,
                                                   cv2.CHAIN_APPROX_SIMPLE)
            
            self.contours = list(contours)
            self.hierarchy = hierarchy
            
            # Simplify if requested
            if simplify:
                self._simplify_contours()
            
            return self.contours
            
        except Exception as e:
            raise RuntimeError(f"Error extracting contours: {str(e)}")
    
    def _simplify_contours(self, epsilon: float = None):
        """
        Simplify contours to reduce number of points
        
        Args:
            epsilon: Contour approximation accuracy
        """
        epsilon = epsilon or config.CONTOUR_APPROXIMATION
        
        simplified = []
        for contour in self.contours:
            if len(contour) > 2:
                area = cv2.contourArea(contour)
                if area >= config.MIN_CONTOUR_AREA:
                    epsilon_dist = epsilon * cv2.arcLength(contour, True)
                    approx = cv2.approxPolyDP(contour, epsilon_dist, True)
                    
                    # Apply contour smoothing
                    if config.SMOOTH_CONTOURS:
                        approx = self._smooth_contour(approx, 
                                                     config.CONTOUR_SMOOTHING_ITERATIONS)
                    
                    simplified.append(approx)
            else:
                simplified.append(contour)
        
        self.simplified_contours = simplified
    
    def _smooth_contour(self, contour: np.ndarray, iterations: int = 3) -> np.ndarray:
        """
        Smooth contour using averaging
        
        Args:
            contour: Contour points
            iterations: Number of smoothing iterations
            
        Returns:
            Smoothed contour
        """
        smoothed = contour.copy()
        
        for _ in range(iterations):
            kernel_size = 5
            smoothed_points = []
            
            for i in range(len(smoothed)):
                if i == 0 or i == len(smoothed) - 1:
                    smoothed_points.append(smoothed[i])
                else:
                    # Average neighboring points
                    neighbor_sum = np.sum(smoothed[max(0, i-2):min(len(smoothed), i+3)], axis=0)
                    avg = neighbor_sum // 5
                    smoothed_points.append(avg)
            
            smoothed = np.array(smoothed_points)
        
        return smoothed
    
    def get_contours(self) -> List[np.ndarray]:
        """
        Get extracted contours
        
        Returns:
            List of contours
        """
        if self.simplified_contours is not None:
            return self.simplified_contours
        elif self.contours is not None:
            return self.contours
        else:
            return self.extract_contours()
    
    def filter_contours_by_area(self, min_area: float = None, 
                                 max_area: float = None) -> List[np.ndarray]:
        """
        Filter contours by area
        
        Args:
            min_area: Minimum contour area
            max_area: Maximum contour area
            
        Returns:
            Filtered contours
        """
        min_area = min_area or config.MIN_CONTOUR_AREA
        filtered = []
        
        for contour in self.get_contours():
            area = cv2.contourArea(contour)
            if min_area <= area <= (max_area or float('inf')):
                filtered.append(contour)
        
        return filtered
    
    def contour_to_path(self, contour: np.ndarray) -> List[Tuple[float, float]]:
        """
        Convert contour to path (list of points)
        
        Args:
            contour: Contour array
            
        Returns:
            List of (x, y) points
        """
        path = []
        for point in contour:
            x, y = point[0]
            path.append((float(x), float(y)))
        return path
    
    def contours_to_paths(self, contours: List[np.ndarray] = None) -> List[List[Tuple]]:
        """
        Convert multiple contours to paths
        
        Args:
            contours: List of contours (uses extracted if None)
            
        Returns:
            List of paths
        """
        contours = contours or self.get_contours()
        paths = []
        
        for contour in contours:
            path = self.contour_to_path(contour)
            if len(path) > 2:  # Only keep paths with at least 3 points
                paths.append(path)
        
        return paths
    
    def get_contour_stats(self, contour: np.ndarray) -> Dict:
        """
        Get statistics for a contour
        
        Args:
            contour: Contour array
            
        Returns:
            Dictionary of contour statistics
        """
        stats = {
            'area': float(cv2.contourArea(contour)),
            'perimeter': float(cv2.arcLength(contour, True)),
            'num_points': len(contour),
            'moments': cv2.moments(contour),
        }
        
        # Calculate bounding rect
        x, y, w, h = cv2.boundingRect(contour)
        stats['bounding_rect'] = {'x': x, 'y': y, 'width': w, 'height': h}
        
        # Calculate approximate dimensions in mm
        stats['bounding_rect_mm'] = {
            'width_mm': pixel_to_mm(w),
            'height_mm': pixel_to_mm(h),
        }
        
        return stats
    
    def draw_contours(self, image: np.ndarray = None, 
                      color: Tuple = (0, 255, 0)) -> np.ndarray:
        """
        Draw contours on image
        
        Args:
            image: Image to draw on (creates new if None)
            color: Color to draw (BGR format)
            
        Returns:
            Image with drawn contours
        """
        if image is None:
            image = np.ones_like(self.binary_image) * 255
        
        contours = self.get_contours()
        cv2.drawContours(image, contours, -1, color, 2)
        
        return image
