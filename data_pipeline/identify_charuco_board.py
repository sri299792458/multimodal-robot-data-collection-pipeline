#!/usr/bin/env python3

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Any

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.calibration.core import _aruco_dictionary, _make_charuco_board, _make_detector_parameters  # noqa: E402


FAMILY_TO_DICTIONARIES: dict[str, tuple[str, ...]] = {
    "4x4": (
        "DICT_4X4_50",
        "DICT_4X4_100",
        "DICT_4X4_250",
        "DICT_4X4_1000",
    ),
    "5x5": (
        "DICT_5X5_50",
        "DICT_5X5_100",
        "DICT_5X5_250",
        "DICT_5X5_1000",
    ),
    "6x6": (
        "DICT_6X6_50",
        "DICT_6X6_100",
        "DICT_6X6_250",
        "DICT_6X6_1000",
    ),
    "7x7": (
        "DICT_7X7_50",
        "DICT_7X7_100",
        "DICT_7X7_250",
        "DICT_7X7_1000",
    ),
    "original": ("DICT_ARUCO_ORIGINAL",),
}

FAMILY_REPRESENTATIVE: dict[str, str] = {
    family: names[-1] for family, names in FAMILY_TO_DICTIONARIES.items()
}

DEFAULT_SEARCH_DICTIONARIES: tuple[str, ...] = tuple(FAMILY_REPRESENTATIVE.values())
DEFAULT_RATIO_VALUES = tuple(round(value, 2) for value in np.arange(0.50, 0.81, 0.01))


@dataclass(frozen=True)
class Candidate:
    dictionary: str
    squares_x: int
    squares_y: int
    marker_ratio: float

    @property
    def dictionary_family(self) -> str:
        return dictionary_family(self.dictionary)

    @property
    def label(self) -> str:
        return (
            f"{self.dictionary_family} "
            f"{self.squares_x}x{self.squares_y} "
            f"marker/square={self.marker_ratio:.2f}"
        )


@dataclass
class CandidateResult:
    candidate: Candidate
    image_hits: int = 0
    total_charuco_corners: int = 0
    total_markers: int = 0
    marker_match_score_sum: float = 0.0
    observed_marker_ids: set[int] = field(default_factory=set)
    best_image_index: int | None = None
    best_marker_corners: Any = None
    best_marker_ids: np.ndarray | None = None
    best_charuco_corners: np.ndarray | None = None
    best_charuco_ids: np.ndarray | None = None

    @property
    def average_marker_match(self) -> float:
        return self.marker_match_score_sum / max(1, self.image_hits)

    @property
    def unique_marker_ids(self) -> int:
        return len(self.observed_marker_ids)

    def score_tuple(self) -> tuple[int, int, int, int, int]:
        return (
            int(self.image_hits),
            int(self.total_charuco_corners),
            int(round(self.average_marker_match * 1000.0)),
            int(self.total_markers),
            int(self.unique_marker_ids),
        )


def dictionary_family(dictionary_name: str) -> str:
    if dictionary_name == "DICT_ARUCO_ORIGINAL":
        return "original"
    for prefix in ("DICT_4X4_", "DICT_5X5_", "DICT_6X6_", "DICT_7X7_"):
        if dictionary_name.startswith(prefix):
            return prefix.split("_")[1].lower()
    return dictionary_name


def _parse_shape(value: str) -> tuple[int, int]:
    normalized = str(value).strip().lower().replace("*", "x")
    parts = [part.strip() for part in normalized.split("x") if part.strip()]
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"Invalid board shape: {value}")
    try:
        squares_x, squares_y = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid board shape: {value}") from exc
    if squares_x < 2 or squares_y < 2:
        raise argparse.ArgumentTypeError(f"Board shape must be at least 2x2: {value}")
    return squares_x, squares_y


def _candidate_shapes(args: argparse.Namespace) -> list[tuple[int, int]]:
    shapes: list[tuple[int, int]] = []
    if args.board_shape:
        for value in args.board_shape:
            shapes.append(_parse_shape(value))
    elif args.squares_x and args.squares_y:
        shapes.append((int(args.squares_x), int(args.squares_y)))
    else:
        shapes.extend([(9, 6), (9, 9), (7, 5), (8, 6), (11, 8), (5, 7), (6, 8)])

    if args.include_transpose:
        expanded: list[tuple[int, int]] = []
        for shape in shapes:
            expanded.append(shape)
            transpose = (shape[1], shape[0])
            if transpose not in expanded:
                expanded.append(transpose)
        shapes = expanded

    deduped: list[tuple[int, int]] = []
    for shape in shapes:
        if shape not in deduped:
            deduped.append(shape)
    return deduped


def _marker_ratio_values(args: argparse.Namespace) -> list[float]:
    if args.marker_ratio:
        return [float(value) for value in args.marker_ratio]

    values: list[float] = []
    current = float(args.marker_ratio_min)
    stop = float(args.marker_ratio_max)
    step = float(args.marker_ratio_step)
    while current <= stop + 1e-9:
        values.append(round(current, 4))
        current += step
    return values or list(DEFAULT_RATIO_VALUES)


def _normalized_dictionary_list(requested: list[str]) -> list[str]:
    dictionaries = [value.strip() for value in requested if value.strip()]
    if dictionaries:
        return dictionaries
    return list(DEFAULT_SEARCH_DICTIONARIES)


def _detect_markers(gray: np.ndarray, dictionary_name: str):
    dictionary = _aruco_dictionary(dictionary_name)
    parameters = _make_detector_parameters()
    if hasattr(parameters, "errorCorrectionRate"):
        # Prefer strict decoding so we can reject wrong families more aggressively.
        parameters.errorCorrectionRate = 0.25
    if hasattr(parameters, "minMarkerPerimeterRate"):
        parameters.minMarkerPerimeterRate = 0.02
    if hasattr(cv2.aruco, "ArucoDetector"):
        detector = cv2.aruco.ArucoDetector(dictionary, parameters)
        return detector.detectMarkers(gray)
    return cv2.aruco.detectMarkers(gray, dictionary, parameters=parameters)


def _render_marker(dictionary_name: str, marker_id: int, size_px: int) -> np.ndarray:
    dictionary = _aruco_dictionary(dictionary_name)
    if hasattr(cv2.aruco, "generateImageMarker"):
        return cv2.aruco.generateImageMarker(dictionary, int(marker_id), int(size_px))
    image = np.zeros((int(size_px), int(size_px)), dtype=np.uint8)
    cv2.aruco.drawMarker(dictionary, int(marker_id), int(size_px), image, 1)
    return image


def _marker_match_score(
    gray: np.ndarray,
    marker_corners,
    marker_ids: np.ndarray | None,
    dictionary_name: str,
    size_px: int = 160,
) -> float:
    if marker_ids is None or len(marker_ids) == 0:
        return 0.0

    dst = np.asarray(
        [
            [0.0, 0.0],
            [size_px - 1.0, 0.0],
            [size_px - 1.0, size_px - 1.0],
            [0.0, size_px - 1.0],
        ],
        dtype=np.float32,
    )
    scores: list[float] = []
    for corners, marker_id in zip(marker_corners, marker_ids.reshape(-1)):
        src = np.asarray(corners, dtype=np.float32).reshape(4, 2)
        transform = cv2.getPerspectiveTransform(src, dst)
        patch = cv2.warpPerspective(gray, transform, (size_px, size_px))
        _, patch_binary = cv2.threshold(patch, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        ideal = _render_marker(dictionary_name, int(marker_id), size_px)
        agreement = np.mean((patch_binary == ideal).astype(np.float32))
        scores.append(float(agreement))
    return float(np.mean(scores)) if scores else 0.0


def _interpolate_charuco(
    gray: np.ndarray,
    marker_corners,
    marker_ids: np.ndarray | None,
    candidate: Candidate,
):
    if marker_ids is None or len(marker_ids) == 0:
        return None, None
    board = _make_charuco_board(
        type(
            "TmpBoard",
            (),
            {
                "squares_x": candidate.squares_x,
                "squares_y": candidate.squares_y,
                "square_length": 1.0,
                "marker_length": candidate.marker_ratio,
                "dictionary": candidate.dictionary,
            },
        )()
    )
    detected, charuco_corners, charuco_ids = cv2.aruco.interpolateCornersCharuco(
        marker_corners,
        marker_ids,
        gray,
        board,
    )
    if detected is None or int(detected) < 4 or charuco_corners is None or charuco_ids is None:
        return None, None
    return charuco_corners, charuco_ids


def _annotate_image(
    image_bgr: np.ndarray,
    marker_corners,
    marker_ids: np.ndarray | None,
    charuco_corners: np.ndarray | None,
    charuco_ids: np.ndarray | None,
    label: str,
) -> np.ndarray:
    annotated = image_bgr.copy()
    if marker_ids is not None and len(marker_ids) > 0:
        cv2.aruco.drawDetectedMarkers(annotated, marker_corners, marker_ids)
    if charuco_corners is not None and charuco_ids is not None:
        cv2.aruco.drawDetectedCornersCharuco(annotated, charuco_corners, charuco_ids)
    cv2.putText(
        annotated,
        label,
        (20, 40),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (0, 255, 0),
        2,
        cv2.LINE_AA,
    )
    return annotated


def _board_marker_count(squares_x: int, squares_y: int, dictionary_name: str) -> int:
    board = _make_charuco_board(
        type(
            "TmpBoard",
            (),
            {
                "squares_x": squares_x,
                "squares_y": squares_y,
                "square_length": 1.0,
                "marker_length": 0.7,
                "dictionary": dictionary_name,
            },
        )()
    )
    return int(len(board.getIds().reshape(-1)))


def _dictionary_capacity(dictionary_name: str) -> int:
    return int(_aruco_dictionary(dictionary_name).bytesList.shape[0])


def _dictionary_prefix_matches(dictionary_a: str, dictionary_b: str, num_markers: int) -> bool:
    bytes_a = _aruco_dictionary(dictionary_a).bytesList
    bytes_b = _aruco_dictionary(dictionary_b).bytesList
    limit = min(int(num_markers), len(bytes_a), len(bytes_b))
    for marker_id in range(limit):
        if not np.array_equal(bytes_a[marker_id], bytes_b[marker_id]):
            return False
    return limit == int(num_markers)


def _compatible_exact_dictionaries(candidate: Candidate, observed_marker_ids: set[int]) -> tuple[list[str], str]:
    family = dictionary_family(candidate.dictionary)
    if family not in FAMILY_TO_DICTIONARIES:
        return [candidate.dictionary], "Exact dictionary compatibility analysis is unavailable for this dictionary."

    family_dictionaries = FAMILY_TO_DICTIONARIES[family]
    standard_board_marker_count = _board_marker_count(
        candidate.squares_x,
        candidate.squares_y,
        FAMILY_REPRESENTATIVE[family],
    )
    standard_compatible = [
        dictionary_name
        for dictionary_name in family_dictionaries
        if _dictionary_capacity(dictionary_name) >= standard_board_marker_count
        and _dictionary_prefix_matches(FAMILY_REPRESENTATIVE[family], dictionary_name, standard_board_marker_count)
    ]

    if len(standard_compatible) == 1:
        return standard_compatible, "Exact dictionary is unique for a standard OpenCV-generated board of this size."

    max_observed_id = max(observed_marker_ids) if observed_marker_ids else -1
    observed_compatible = [
        dictionary_name
        for dictionary_name in family_dictionaries
        if _dictionary_capacity(dictionary_name) > max_observed_id
    ]
    if observed_compatible == standard_compatible:
        note = (
            "Exact dictionary is ambiguous from photos alone for a standard OpenCV-generated board of this size. "
            f"The first {standard_board_marker_count} markers are shared across: {', '.join(standard_compatible)}."
        )
    else:
        note = (
            "Exact dictionary is ambiguous from photos alone. "
            f"Observed marker IDs are compatible with: {', '.join(observed_compatible)}. "
            f"For a standard OpenCV-generated board with {standard_board_marker_count} markers, compatible dictionaries are: "
            f"{', '.join(standard_compatible)}."
        )
    return standard_compatible, note


def _fronto_parallel_board_score(
    gray: np.ndarray,
    marker_corners,
    marker_ids: np.ndarray | None,
    candidate: Candidate,
    cell_px: int = 120,
) -> float:
    charuco_corners, charuco_ids = _interpolate_charuco(gray, marker_corners, marker_ids, candidate)
    if charuco_corners is None or charuco_ids is None:
        return -1.0

    board = _make_charuco_board(
        type(
            "TmpBoard",
            (),
            {
                "squares_x": candidate.squares_x,
                "squares_y": candidate.squares_y,
                "square_length": 1.0,
                "marker_length": candidate.marker_ratio,
                "dictionary": candidate.dictionary,
            },
        )()
    )
    board_points = np.asarray(board.getChessboardCorners()[charuco_ids.reshape(-1)], dtype=np.float32)[:, :2]
    image_points = np.asarray(charuco_corners, dtype=np.float32).reshape(-1, 2)
    if len(board_points) < 4:
        return -1.0

    homography, _ = cv2.findHomography(board_points, image_points, method=0)
    if homography is None:
        return -1.0

    width = int(candidate.squares_x * cell_px)
    height = int(candidate.squares_y * cell_px)
    scale = np.asarray(
        [
            [float(cell_px), 0.0, 0.0],
            [0.0, float(cell_px), 0.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    board_pixels_to_image = homography @ np.linalg.inv(scale)
    warped = cv2.warpPerspective(
        gray,
        np.linalg.inv(board_pixels_to_image),
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=255,
    )
    _, warped_binary = cv2.threshold(warped, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ideal = board.generateImage((width, height), marginSize=0)

    margin = max(4, cell_px // 20)
    warped_crop = warped_binary[margin : height - margin, margin : width - margin]
    ideal_crop = ideal[margin : height - margin, margin : width - margin]
    return float(np.mean((warped_crop == ideal_crop).astype(np.float32)))


def _refine_marker_ratio(
    gray_images: list[np.ndarray],
    marker_detections: dict[tuple[int, str], tuple[Any, np.ndarray | None]],
    base_candidate: Candidate,
    marker_ratios: list[float],
) -> tuple[float, float]:
    scored: list[tuple[float, float]] = []
    for marker_ratio in marker_ratios:
        candidate = Candidate(
            dictionary=base_candidate.dictionary,
            squares_x=base_candidate.squares_x,
            squares_y=base_candidate.squares_y,
            marker_ratio=float(marker_ratio),
        )
        image_scores: list[float] = []
        for image_index, gray in enumerate(gray_images):
            marker_corners, marker_ids = marker_detections[(image_index, base_candidate.dictionary)]
            if marker_ids is None or len(marker_ids) == 0:
                continue
            score = _fronto_parallel_board_score(gray, marker_corners, marker_ids, candidate)
            if score >= 0.0:
                image_scores.append(score)
        if image_scores:
            scored.append((float(np.mean(image_scores)), float(marker_ratio)))
    if not scored:
        return base_candidate.marker_ratio, -1.0
    scored.sort(reverse=True)
    best_score, best_ratio = scored[0]
    return best_ratio, best_score


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Identify the most likely ChArUco board family and layout from one or more photos. "
            "The script can usually determine board shape and dictionary family, but exact "
            "OpenCV dictionary size (for example 6X6_50 vs 6X6_250) may be ambiguous."
        )
    )
    parser.add_argument("images", nargs="+", help="Path(s) to clear ChArUco board photos.")
    parser.add_argument(
        "--board-shape",
        action="append",
        default=[],
        help="Candidate board shape like 9x6. Repeat to try multiple shapes.",
    )
    parser.add_argument("--squares-x", type=int, default=0, help="Known board squares_x.")
    parser.add_argument("--squares-y", type=int, default=0, help="Known board squares_y.")
    parser.add_argument(
        "--include-transpose",
        action="store_true",
        help="Also test swapped orientations like 6x9 when 9x6 is provided.",
    )
    parser.add_argument(
        "--dictionary",
        action="append",
        default=[],
        help=(
            "Candidate dictionary names. Repeat to try multiple exact dictionaries. "
            "If omitted, the script searches one representative dictionary per family."
        ),
    )
    parser.add_argument(
        "--marker-ratio",
        type=float,
        action="append",
        default=[],
        help="Candidate marker_length / square_length ratio. Repeat to try multiple.",
    )
    parser.add_argument("--marker-ratio-min", type=float, default=0.50)
    parser.add_argument(
        "--marker-ratio-max",
        type=float,
        default=0.80,
        help="Default max is 0.80 because larger ratios are usually unstable for ChArUco detection.",
    )
    parser.add_argument("--marker-ratio-step", type=float, default=0.01)
    parser.add_argument("--top-k", type=int, default=10, help="How many ranked candidates to print.")
    parser.add_argument(
        "--save-best-annotated-dir",
        default="",
        help="Optional directory to save annotated copies for the best candidate.",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()

    image_paths = [Path(path).expanduser() for path in args.images]
    loaded_images: list[tuple[Path, np.ndarray, np.ndarray]] = []
    for image_path in image_paths:
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            raise FileNotFoundError(f"Failed to read image: {image_path}")
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        loaded_images.append((image_path, image, gray))

    dictionaries = _normalized_dictionary_list(args.dictionary)
    shapes = _candidate_shapes(args)
    marker_ratios = _marker_ratio_values(args)

    marker_detections: dict[tuple[int, str], tuple[Any, np.ndarray | None]] = {}
    marker_stats: dict[tuple[int, str], tuple[int, float]] = {}
    for image_index, (_, _, gray) in enumerate(loaded_images):
        for dictionary_name in dictionaries:
            marker_corners, marker_ids, _ = _detect_markers(gray, dictionary_name)
            marker_detections[(image_index, dictionary_name)] = (marker_corners, marker_ids)
            marker_count = len(marker_ids) if marker_ids is not None else 0
            match_score = _marker_match_score(gray, marker_corners, marker_ids, dictionary_name) if marker_count else 0.0
            marker_stats[(image_index, dictionary_name)] = (marker_count, match_score)

    results: list[CandidateResult] = []
    for dictionary_name in dictionaries:
        for squares_x, squares_y in shapes:
            for marker_ratio in marker_ratios:
                candidate = Candidate(
                    dictionary=dictionary_name,
                    squares_x=squares_x,
                    squares_y=squares_y,
                    marker_ratio=float(marker_ratio),
                )
                result = CandidateResult(candidate=candidate)
                for image_index, (_, _, gray) in enumerate(loaded_images):
                    marker_corners, marker_ids = marker_detections[(image_index, dictionary_name)]
                    marker_count, marker_match_score = marker_stats[(image_index, dictionary_name)]
                    if marker_count == 0:
                        continue
                    charuco_corners, charuco_ids = _interpolate_charuco(gray, marker_corners, marker_ids, candidate)
                    if charuco_corners is None or charuco_ids is None:
                        continue
                    charuco_count = int(len(charuco_ids))
                    result.image_hits += 1
                    result.total_charuco_corners += charuco_count
                    result.total_markers += marker_count
                    result.marker_match_score_sum += float(marker_match_score)
                    result.observed_marker_ids.update(int(value) for value in marker_ids.reshape(-1))
                    previous_best_count = 0 if result.best_charuco_ids is None else int(len(result.best_charuco_ids))
                    if result.best_image_index is None or charuco_count > previous_best_count:
                        result.best_image_index = image_index
                        result.best_marker_corners = marker_corners
                        result.best_marker_ids = marker_ids
                        result.best_charuco_corners = charuco_corners
                        result.best_charuco_ids = charuco_ids
                if result.image_hits > 0:
                    results.append(result)

    if not results:
        print("No ChArUco candidate produced a valid interpolation.")
        print("Try a clearer front-facing image, or widen the candidate shapes/dictionaries.")
        return 1

    ranked = sorted(results, key=lambda item: item.score_tuple(), reverse=True)
    best = ranked[0]
    refined_ratio, refined_ratio_score = _refine_marker_ratio(
        [gray for _, _, gray in loaded_images],
        marker_detections,
        best.candidate,
        marker_ratios,
    )
    refined_best = Candidate(
        dictionary=best.candidate.dictionary,
        squares_x=best.candidate.squares_x,
        squares_y=best.candidate.squares_y,
        marker_ratio=refined_ratio,
    )
    compatible_exact, compatibility_note = _compatible_exact_dictionaries(
        refined_best,
        best.observed_marker_ids,
    )

    print("Most likely ChArUco board family/layout candidates (coarse ratio sweep):")
    print()
    for index, result in enumerate(ranked[: max(1, int(args.top_k))], start=1):
        candidate = result.candidate
        print(
            f"{index:02d}. {candidate.label} | "
            f"search_dictionary={candidate.dictionary} "
            f"marker_match={result.average_marker_match:.3f} "
            f"image_hits={result.image_hits} "
            f"charuco_corners={result.total_charuco_corners} "
            f"markers={result.total_markers} "
            f"unique_marker_ids={result.unique_marker_ids}"
        )

    print()
    print("Best candidate:")
    print(f"  dictionary_family: {refined_best.dictionary_family}")
    print(f"  representative_search_dictionary: {refined_best.dictionary}")
    print(f"  squares_x: {refined_best.squares_x}")
    print(f"  squares_y: {refined_best.squares_y}")
    print(f"  estimated_marker_length / square_length: {refined_best.marker_ratio:.2f}")
    if refined_ratio_score >= 0.0:
        print(f"  ratio_fit_score: {refined_ratio_score:.3f}")
    print(f"  compatible_exact_dictionaries: {', '.join(compatible_exact)}")
    print()
    print("Important:")
    print("  The ranked list above is only the coarse search stage; the final ratio below is refined afterwards.")
    print(f"  {compatibility_note}")
    print("  This script does not infer physical meter sizes.")
    print("  You still need to measure square_length and marker_length on the real board.")

    if args.save_best_annotated_dir:
        output_dir = Path(args.save_best_annotated_dir).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)
        for image_index, (image_path, image_bgr, gray) in enumerate(loaded_images):
            marker_corners, marker_ids = marker_detections[(image_index, refined_best.dictionary)]
            charuco_corners, charuco_ids = _interpolate_charuco(
                gray,
                marker_corners,
                marker_ids,
                refined_best,
            )
            annotated = _annotate_image(
                image_bgr,
                marker_corners,
                marker_ids,
                charuco_corners,
                charuco_ids,
                refined_best.label,
            )
            output_path = output_dir / f"{image_path.stem}.annotated.png"
            cv2.imwrite(str(output_path), annotated)
            print(f"Saved annotated image: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
