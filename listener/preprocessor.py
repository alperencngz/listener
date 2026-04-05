"""Audio preprocessing — noise reduction before transcription.

Uses noisereduce with non-stationary mode for meeting audio.
Non-stationary adapts the noise floor over time, which handles
varying background noise (HVAC cycling, cafe chatter, etc.).
"""

import logging
from pathlib import Path

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)


def preprocess_audio(input_path: str, output_path: str | None = None) -> str:
    """Apply noise reduction to an audio file.

    Args:
        input_path: Path to the input WAV file.
        output_path: Path for the cleaned output file.
            If None, defaults to input_path with '_cleaned' suffix.

    Returns:
        Path to the cleaned audio file.
    """
    import noisereduce as nr

    if output_path is None:
        p = Path(input_path)
        output_path = str(p.with_stem(p.stem + "_cleaned"))

    logger.info("Preprocessing audio: %s -> %s", input_path, output_path)

    data, sr = sf.read(input_path)

    # Convert stereo to mono if needed
    if data.ndim > 1:
        data = np.mean(data, axis=1)

    # Non-stationary noise reduction (adapts over time — good for meetings)
    # prop_decrease=0.75: moderate reduction. 1.0 can distort speech harmonics
    # and actually hurt Whisper accuracy.
    cleaned = nr.reduce_noise(
        y=data,
        sr=sr,
        stationary=False,
        prop_decrease=0.75,
        time_constant_s=2.0,
        freq_mask_smooth_hz=500,
    )

    sf.write(output_path, cleaned, sr)
    logger.info("Noise reduction complete: %s", output_path)
    return output_path
