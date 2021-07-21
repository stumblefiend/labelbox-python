import logging
import os
import urllib.request
from typing import Callable, Dict, Generator, Optional, Tuple
from uuid import uuid4

import cv2
import numpy as np
from pydantic import root_validator

from .base_data import BaseData

logger = logging.getLogger(__name__)


class VideoData(BaseData):
    """
    Represents video
    """
    file_path: Optional[str] = None
    url: Optional[str] = None
    frames: Optional[Dict[int, np.ndarray]] = None

    def load_frames(self, overwrite: bool = False) -> None:
        """
        Loads all frames into memory at once in order to access in non-sequential order.
        This will use a lot of memory, especially for longer videos

        Args:
            overwrite: Replace existing frames
        """
        if self.frames and not overwrite:
            return

        for count, frame in self.frame_generator():
            self.frames[count] = frame

    def frame_generator(
            self,
            cache_frames=False,
            download_dir='/tmp'
    ) -> Generator[Tuple[int, np.ndarray], None, None]:
        """
        A generator for accessing individual frames in a video.

        Args:
            cache_frames (bool): Whether or not to cache frames while iterating through the video.
            download_dir (str): Directory to save the video to. Defaults to `/tmp` dir
        """
        if self.frames is not None:
            for idx, img in self.frames.items():
                yield idx, img
            return
        elif self.url and not self.file_path:
            file_path = os.path.join(download_dir, f"{uuid4()}.mp4")
            logger.info("Downloading the video locally to %s", file_path)
            urllib.request.urlretrieve(self.url, file_path)
            self.file_path = file_path

        vidcap = cv2.VideoCapture(self.file_path)

        success, img = vidcap.read()
        count = 0
        self.frames = {}
        while success:
            img = img[:, :, ::-1]
            yield count, img
            if cache_frames:
                self.frames[count] = img
            success, img = vidcap.read()
            count += 1

    def __getitem__(self, idx: int) -> np.ndarray:
        if self.frames is None:
            raise ValueError(
                "Cannot select by index without iterating over the entire video or loading all frames."
            )
        return self.frames[idx]

    def create_url(self, signer: Callable[[bytes], str]) -> None:
        """
        Utility for creating a url from any of the other video references.

        Args:
            signer: A function that accepts bytes and returns a signed url.
        Returns:
            url for the video
        """
        if self.url is not None:
            return self.url
        elif self.file_path is not None:
            with open(self.file_path, 'rb') as file:
                self.url = signer(file.read())
        elif self.frames is not None:
            self.file_path = self.frames_to_video(self.frames)
            self.url = self.create_url(signer)
        else:
            raise ValueError(
                "One of url, im_bytes, file_path, numpy must not be None.")
        return self.url

    def frames_to_video(self,
                        frames: Dict[int, np.ndarray],
                        fps=20,
                        save_dir='/tmp') -> str:
        """
        Compresses the data by converting a set of individual frames to a single video.

        """
        file_path = os.path.join(save_dir, f"{uuid4()}.mp4")
        out = None
        for key in frames.keys():
            frame = frames[key]
            if out is None:
                out = cv2.VideoWriter(file_path,
                                      cv2.VideoWriter_fourcc(*'MP4V'), fps,
                                      frame.shape[:2])
            out.write(frame)
        if out is None:
            return
        out.release()
        return file_path

    @root_validator
    def validate_data(cls, values):
        file_path = values.get("file_path")
        url = values.get("url")
        frames = values.get("frames")
        uid = values.get("uid")

        if uid == file_path == frames == url == None:
            raise ValueError(
                "One of `file_path`, `frames`, `uid`, or `url` required.")
        return values

    class Config:
        # Required for numpy arrays
        arbitrary_types_allowed = True
        # Required for discriminating between data types
        extra = 'forbid'
