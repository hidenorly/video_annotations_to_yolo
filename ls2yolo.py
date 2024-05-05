"""
This script processes video annotations created using Label Studio, converting them into a 
format suitable for the YOLO object detection model. The script has the capability to interpolate
bounding boxes for each intermediate frame based on key-frame annotations (as needed), and export 
these labels (i.e., bounding box coordinates), along with the corresponding frames, into a 
YOLO-compatible format. As it stands with Label Studio version 1.7.0, such functionality isn't 
inherently available. Please note that video annotations should be exported in the JSON-MIN format.
"""

import argparse
import os
import json
import csv
import copy
import cv2
from decimal import Decimal
from pathlib import Path
from tqdm import tqdm


def linear_interpolation(prev_seq, seq, label):
    # Define the start and end frame numbers
    a0 = prev_seq['frame']
    a1 = seq['frame']
    frames_info = dict()
    # Loop over all intermediate frames
    for frame in range(a0+1, a1):
        t = Decimal(frame-a0)/Decimal(a1-a0)
        info = [label]
        # Interpolate bounding box dimensions for the current frame
        for b0, b1 in ((prev_seq[k], seq[k]) for k in ('x', 'y', 'width', 'height')):
            info.append(str(b0 + t*(b1-b0)))
        # Add interpolated information for the current frame to 'frames_info'
        frames_info[frame] = info
    return frames_info

def main(video_label, output_base, offset=0, labels=set()):
    video_path = video_label["video"]
    labels_dict = {k:i for i,k in enumerate(labels)}

    # extract count of frames
    max_frames = 0
    for subject in video_label['box']:
        if "framesCount" in subject:
            _frame_count = int( subject["framesCount"] )
            if _frame_count > max_frames:
                max_frames = _frame_count

    # prepare metadata available frame flags
    availableFrames = [False] * (max_frames+1)
    for subject in video_label['box']:
        start_frame = subject['sequence'][0]["frame"]
        end_frame = subject['sequence'][len(subject['sequence'])-1]["frame"]
        print(f'{subject['labels'][0]} : {start_frame+offset} - {end_frame+offset}')
        for i in range(start_frame, end_frame):
            availableFrames[i] = True

    # Initialize dictionaries to store file information and frame timestamps
    files_dict = dict()
    frame_times = dict()

    # Loop over the subjects, i.e. football players in a match
    for subject in copy.deepcopy(video_label['box']):
        # Get the subject labels (e.g. team-A, team-B, referee, ball)
        subject_labels = subject['labels']

        # Map the label to its integer representation
        if len(subject_labels)==1:
            label = labels_dict[subject_labels[0]]
        else:
            raise ValueError("Each subject must have exactly one label.")
        
        prev_seq = None

        # Process each sequence in the subject's timeline
        for seq in subject['sequence']:
            frame = seq['frame']

            # Adjust the x and y coordinates to be the center of the bounding box
            seq['x'] += seq['width'] / Decimal('2')
            seq['y'] += seq['height'] / Decimal('2')

            # Adjust the scale of bounding box dimensions
            for k in ('x', 'y', 'width', 'height'):
                seq[k] /= Decimal('100')

            # If the current sequence is not adjacent to the previous sequence, perform linear interpolation
            if (prev_seq is not None) and prev_seq['enabled'] and (frame - prev_seq['frame'] > 1):
                lines = linear_interpolation(prev_seq, seq, label)
            else:
                lines = dict()

            # Create the bounding box information line for the current frame
            lines[frame] = [label] + [str(seq[k]) for k in ('x', 'y', 'width', 'height')]

            # Add the bounding box information line to the corresponding frame in 'files_dict'
            for frame, info in lines.items():
                if frame in files_dict:
                    files_dict[frame].append(info)
                else:
                    files_dict[frame] = [info]

            # Store the timestamp for the current frame
            frame_times.update({frame:float(seq['time'])})

            prev_seq = seq

    # Sort the file information and frame timestamp dictionaries
    files_dict = dict(sorted(files_dict.items()))
    frame_times = dict(sorted(frame_times.items()))

    print("Exporting annotations in YOLO format")

    # Prepare YOLO directory structure
    output_path = Path(output_base)
    [(output_path / p).mkdir(parents=True, exist_ok=True) for p in ('images/', 'labels/')]

    max_frame = max(files_dict.keys())
    padding = len(str(max_frame))

    # Write the YOLO labels
    for frame, lines in files_dict.items():
        if availableFrames[frame]:
            with open(output_path / 'labels' / f'frame_{offset+frame:0{padding}d}.txt', 'w') as csvfile:
                csvwriter = csv.writer(csvfile, delimiter=' ')
                csvwriter.writerows(lines)

    # Extract the Frames
    if os.path.isfile(video_path):
        vidcap = cv2.VideoCapture(video_path)
        print(f'Extracting frames')
        for frame in tqdm(files_dict):
            if availableFrames[frame]:
                vidcap.set(cv2.CAP_PROP_POS_FRAMES, frame-1)
                success, image = vidcap.read()
                if success:
                    cv2.imwrite(str(output_path / 'images' / f'frame_{offset+frame:0{padding}d}.jpg'), image)
                else:
                    print(f"Unable to read frame {frame}. Quiting.")
                    break
    
    print("Process finished successfully.")

    return offset+len(tqdm(files_dict))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="""This script processes video annotations exported from Label Studio
        in JSON-MIN format, converting them into a YOLO-compatible format. The script supports 
        interpolation of bounding boxes for intermediate frames based on key-frame annotations 
        and exports these labels along with corresponding frames (if a video path is provided).""",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
        )
    parser.add_argument("-j", "--json_path", required=True, help="Path to JSON annotations")
    parser.add_argument("-v", "--video_path", default=None, help="Optional path to video file."\
        " If provided, corresponding frames will be extracted.")
    parser.add_argument("-o", "--output_base", default='output/', help="Path to output base directory")
    args = parser.parse_args()

    print("Parsing annotations from JSON")
    video_labels = []
    with open(args.json_path) as f:
        video_labels = json.load(f, parse_float=Decimal)
        # replace from video uri in the export file to actual path
        for video in video_labels:
            if "video" in video:
                video_path = video["video"]
                pos = video_path.rfind("/")
                if pos!=None:
                    video_path = os.path.join(args.video_path, video_path[pos+1:])
                    if os.path.isfile(video_path):
                        video["video"] = video_path

    # extract labels
    labels = set()
    for video_label in video_labels:
        for subject in video_label['box']:
            labels.add(*subject['labels'])
    labels = sorted(labels)
    print(f'labels = {labels}')

    # extract video frames and output yolo formated frame_xxxx.txt
    frame_count = 0
    for video_label in video_labels:
        print(f'{video_label["video"]}......')
        frame_count += main(video_label, args.output_base, frame_count, labels)

    # Write the YOLO classes
    with open(os.path.join(args.output_base, f'classes.txt'), 'w') as f:
        f.writelines(f'{line}\n' for line in labels)