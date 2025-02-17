# The Python file contains functions for initializing and using a PyTorch model to detect objects in a frame.

import os
import cv2
import math
import torch
import numpy as np
from scipy.spatial import distance
from scipy.spatial.distance import cdist
from yolov7.models.experimental import attempt_load
from yolov7.utils.general import non_max_suppression, scale_coords, check_img_size
from yolov7.utils.torch_utils import time_synchronized, select_device

from configs import options, SHOW_CENTER_POINTS, SHOW_DIRECTION_ARROW, SHOW_DIRECTION_LABEL, SHOW_CLASS_LABEL
from image_functions import adjust_image_to_desired_shape

def initialize_player_detection_model():
    """
    Initializes the player detection model with the provided weights and configuration options.

    Args:
        None

    Returns:
    - weights (str): The path of the weight file used for the model.
    - img_size (int): The image size used for the model.
    - device (torch.device): The device on which the model is running.
    - use_half_precision (bool): A boolean value indicating whether half precision is used for the model.
    - model (torch.nn.Module): The player detection model.
    - stride (int): The stride value used for the model.
    - names (list): The list of class names used for the model.
    - classes (list): The list of class indexes corresponding to the class names.

    Raises:
        None
    """
    weights, img_size = options['weights'], options['img-size']
    device = select_device(options['device'])
    use_half_precision = device.type != 'cpu'
    model = attempt_load(weights, map_location=device)
    stride = int(model.stride.max())
    img_size = check_img_size(img_size, s=stride)
    if use_half_precision:
        model.half()

    names = model.module.names if hasattr(model, 'module') else model.names
    if device.type != 'cpu':
        model(torch.zeros(1, 3, img_size, img_size).to(
            device).type_as(next(model.parameters())))

    # Convert class names to class indexes
    classes = []
    for class_name in options['classes']:
        classes.append(names.index(class_name))

    return weights, img_size, device, use_half_precision, model, stride, names, classes


def detect_objects(
        frame,
        prev_person_center_points,
        player_with_the_ball_center_point,
        img_size,
        device,
        use_half_precision,
        model,
        stride,
        names,
        frames_counter,
        prev_angles,
        FIRST_FRAME_SAVED,
        CURRENT_TIMESTAMP,
        FIELD_COORDINATES,
        GOALS):
    """
    Detects objects in a frame using a PyTorch model.

    Args:
    - frame (numpy.ndarray): The frame to detect objects in.
    - prev_person_center_points (list of tuples of int): The center points of the person objects from previous frame.
    - player_with_the_ball_center_point (tuple of int): The center point of the player with the ball.
    - img_size (int): The size of the input image for the model.
    - device (str): The device to use for inference.
    - use_half_precision (bool): Whether to use half-precision or not.
    - model (torch.nn.Module): The PyTorch model to use for object detection.
    - stride (int): The stride used for adjusting the image shape.
    - names (list of str): The class names of the objects to detect.
    - frames_counter (int): Counter of the current frame.
    - prev_angles (list of lists): List of angles of movement direction of players from the previous frame.
    - FIRST_FRAME_SAVED (bool): Boolean that represents whether the first frame of the game was saved.
    - CURRENT_TIMESTAMP (str): Timestamp unique for each game.
    - FIELD_COORDINATES (list of objects): List of 4 objects that represent coordinates of field corners.
    - GOALS (list of objects): List of 2 objects that represent coordinates of two goals. 

    Returns:
    - player_with_the_ball_center_point (tuple of int): The center point of the player with the ball.
    - prev_person_center_points (list of tuples of int): The center points of the person objects from previous frame.
    - players_list_indexes_direction_playerWithTheBall (list of lists):Coordinates of player with the ball.
    - ball_indexes (list): Coordinates of the ball.
    - frames_counter (int): Counter of the current frame.
    - angles (list of lists): List of angles of movement direction of players.
    - FIRST_FRAME_SAVED (bool): Boolean that represents whether the first frame of the game was saved.

    Raises:
        None
    """
    # Convert image to desired shape and format
    img = adjust_image_to_desired_shape(frame, img_size, stride=stride)[0]
    img = img[:, :, ::-1].transpose(2, 0, 1)  # BGR to RGB, to 3x416x416
    img = np.ascontiguousarray(img)

    # Convert image to PyTorch tensor
    img = torch.from_numpy(img).to(device)
    img = img.half() if use_half_precision else img.float()

    # Normalize image pixel values to 0-1 range
    img /= 255.0

    # Add a forth dimention to the image object (batch_size=1)
    if img.ndimension() == 3:
        img = img.unsqueeze(0)

    # Inference
    t1 = time_synchronized()
    detection_results = model(img, augment=False)[0]
    person_detection_results = non_max_suppression(
        detection_results, options['class-person']['conf-thres'], options['iou-thres'], classes=names.index(options['class-person']['class-name']), agnostic=False)
    ball_detection_results = non_max_suppression(
        detection_results, options['class-ball']['conf-thres'], options['iou-thres'], classes=names.index(options['class-ball']['class-name']), agnostic=False)
    all_detections = [
        torch.cat((person_detection_results[0], ball_detection_results[0]))]
    t2 = time_synchronized()

    # Draw field coordinates circles
    for coordinate in FIELD_COORDINATES:
        cv2.circle(frame, (coordinate["x"], coordinate["y"]), 3, (0, 0, 255), -1)

    # Draw goals
    for goal in GOALS:
        goal_point1 = (goal["x1"], goal["y1"])
        goal_point2 = (goal["x2"], goal["y2"])
        cv2.line(frame, goal_point1, goal_point2, (0, 0, 255), 1)

    # Scale box coordinates to the size of current frame
    for detection in person_detection_results:
        detection[:, :4] = scale_coords(img.shape[2:], detection[:, :4], frame.shape).round()
    for detection in ball_detection_results:
        detection[:, :4] = scale_coords(img.shape[2:], detection[:, :4], frame.shape).round() 
    for detection in all_detections:
        detection[:, :4] = scale_coords(img.shape[2:], detection[:, :4], frame.shape).round() 

    if not FIRST_FRAME_SAVED:
        path = '../materials/traces/' + CURRENT_TIMESTAMP.strftime('%Y-%m-%d_%H-%M-%S') + '/first_frame.jpg'
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cv2.imwrite(path, frame)
        FIRST_FRAME_SAVED = True

    # Plotting the detections
    players_list_indexes_direction_playerWithTheBasll = []
    ball_indexes = []
    angles = []
    for detection in all_detections:
        if not len(detection):
            continue

        player_with_the_ball_center_point = _detect_player_with_the_ball(
            detection, names, player_with_the_ball_center_point)
        prev_person_center_points, angles, frames_counter  = _detect_players_moving_direction(frame,
            prev_person_center_points, person_detection_results, frames_counter, prev_angles)
        
        # Draw the class name label and center point for each object
        for i, (x1, y1, x2, y2, confidence_score, class_id) in enumerate(detection):
            center_x = int((x1 + x2) // 2)
            center_y = int((y1 + y2) // 2)

            # Assign color based on object class
            playerWithTheBall = False
            class_name = names[int(class_id)]
            if player_with_the_ball_center_point and center_x == player_with_the_ball_center_point[0] and center_y == player_with_the_ball_center_point[1]:
                playerWithTheBall = True
                text_color = (255, 255, 0)  # blue for player with the ball
            elif class_name == 'sports ball':
                text_color = (0, 255, 0)  # green for ball
                class_name = 'ball'
            else:
                text_color = (0, 0, 255)  # red for rest of the players
            
            # Output the arrow to show the direction
            if SHOW_DIRECTION_ARROW:
                if len(angles) > i and class_name == 'person':
                    arrow_angle = angles[i]
                    start_arrow = (center_x, center_y)
                    arrow_length = 20
                    delta_x = arrow_length * math.cos(math.radians(arrow_angle))
                    delta_y = arrow_length * math.sin(math.radians(arrow_angle))
                    x2 = int(center_x + delta_x)
                    y2 = int(center_y - delta_y)
                    end_arrow = (x2, y2)
                    cv2.arrowedLine(frame, start_arrow, end_arrow, (0, 0, 255), 1)

            # Create text label and display it on the frame
            if SHOW_CLASS_LABEL:
                class_label = f'{class_name} ({center_x}, {center_y})'
                cv2.putText(frame, class_label, (center_x + 10, center_y - 25),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, text_color, 1)
          
            if SHOW_DIRECTION_LABEL:
                direction_text = f'direction:{angles[i] + 180},' if class_name == 'person' else ''
                direction_label = f'{direction_text} conf:{confidence_score:.2f}'
                cv2.putText(frame, direction_label, (center_x + 10, center_y - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, text_color, 1)                   
                cv2.putText(frame, direction_text, (center_x + 10, center_y - 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, text_color, 1)
            
            if(class_name == 'ball'):
                ball_indexes.append({"x":center_x, "y":center_y})
                
            if playerWithTheBall == True and class_name == 'person' and len(angles) > i:
                player = {
                    "holdsBall": True,
                    "x": center_x,
                    "y": int(y1),
                    "sightDirection": float(angles[i]),
                    "center_y": center_y
                }
                players_list_indexes_direction_playerWithTheBasll.append(
                    player)

            elif playerWithTheBall == False and class_name == 'person' and len(angles) > i:
                player = {
                    "holdsBall": False,
                    "x": center_x,
                    "y": int(y1),
                    "sightDirection": float(angles[i]),
                    "center_y": center_y
                }
                players_list_indexes_direction_playerWithTheBasll.append(
                    player)

            # Draw center point circle on the frame
            if SHOW_CENTER_POINTS:
                cv2.circle(frame, (center_x, center_y), 3, (0, 0, 255), -1)

    return player_with_the_ball_center_point, prev_person_center_points, players_list_indexes_direction_playerWithTheBasll, ball_indexes, frames_counter, angles, FIRST_FRAME_SAVED


def _find_nearest_player_to_the_ball(ball_center_point, all_detections, names):
    if not ball_center_point:
        return None

    # Precompute ball coordinates as a NumPy array
    ball_coordinates_np = np.array(
        [ball_center_point[0].cpu(), ball_center_point[1].cpu()])

    # Filter out non-person detections before the loop
    person_detections = [(x, y, w, h, conf, cls_id) for x, y, w, h,
                         conf, cls_id in all_detections if names[int(cls_id)] == 'person']

    min_distance_to_ball = float('inf')
    nearest_player_center_point = None

    for x, y, w, h, conf, cls_id in reversed(person_detections):
        player_center_point = ((x + w) // 2, (y + h) // 2)
        player_coordinates_np = np.array(
            [player_center_point[0].cpu(), player_center_point[1].cpu()])
        euclidean_distance_sq = distance.sqeuclidean(
            ball_coordinates_np, player_coordinates_np)

        if euclidean_distance_sq < min_distance_to_ball:
            min_distance_to_ball = euclidean_distance_sq
            nearest_player_center_point = (x, y, w, h)

    return nearest_player_center_point if min_distance_to_ball != float('inf') else None


def _detect_player_with_the_ball(detection, names, player_with_the_ball_center_point):
    # Detect the player with the ball - the player whose coordinates are
    # the nearest to the found ball.
    ball_found_in_current_frame = False
    nearest_player_to_the_ball = None
    for *xyxy, confidence_score, class_id in reversed(detection):
        if (names[int(class_id)] == 'sports ball'):
            ball_found_in_current_frame = True
            center_point = ((xyxy[0] + xyxy[2]) // 2, (xyxy[1] + xyxy[3]) // 2)
            nearest_player_to_the_ball = _find_nearest_player_to_the_ball(
                center_point, detection, names)

    # If the ball was not found in the current frame - the 'player with the ball'
    # will be the player whose coordinates are the nearest to the 'player with the ball'
    # in the previous frame.
    if not ball_found_in_current_frame:
        for *xyxy, confidence_score, class_id in reversed(detection):
            nearest_player_to_the_ball = _find_nearest_player_to_the_ball(
                player_with_the_ball_center_point, detection, names)

    if nearest_player_to_the_ball:
        player_with_the_ball_center_point = (
            (nearest_player_to_the_ball[0] + nearest_player_to_the_ball[2]) // 2, (nearest_player_to_the_ball[1] + nearest_player_to_the_ball[3]) // 2)

    return player_with_the_ball_center_point


def _detect_players_moving_direction(frame, prev_players_center_points, curr_players, frames_counter, prev_angles):
    # Calculate center points of current person objects
    curr_players_xyxy = curr_players[0][:, :4]
    curr_players_center_points = [[((bbox[0]+bbox[2])//2), ((bbox[1]+bbox[3])//2)]
                                  for bbox in curr_players_xyxy.cpu().numpy()]

    frames_counter += 1

    # It's the first frame
    if prev_players_center_points is None:
        return curr_players_center_points, [0] * len(curr_players_center_points), frames_counter

    # If no players detected or the frame count is odd - assume the missing players are in the same coordinates
    # (we're skipping each second frame for better direction accuracy)
    if not len(curr_players_center_points) or (frames_counter % 2 != 0):
        return prev_players_center_points, prev_angles, frames_counter    # PROBLEM IS HERE, I'M RETURNING 000000

    # Find the index of the closest point in the first vector for each point in the second vector
    if prev_players_center_points == []:
        return curr_players_center_points, [0] * len(curr_players_center_points), frames_counter
    closest_indices = np.argmin(
        cdist(prev_players_center_points, curr_players_center_points), axis=0)

    # If detected less players than in the previous frame - assume the missing players are in the same coordinates
    indices_to_add = []
    if len(prev_players_center_points) > len(curr_players_center_points):
        for i in range(len(prev_players_center_points)):
            if i not in closest_indices:
                curr_players_center_points.append(
                    prev_players_center_points[i])
                indices_to_add.append(i)

    closest_indices = list(np.append(closest_indices, indices_to_add).astype(int))

    # Calculate the motion angles of each player
    angles = []
    for i, curr_player in enumerate(curr_players_center_points):
        prev_player = prev_players_center_points[closest_indices[i]]

        # Draw points of current and previous coordinate
        if SHOW_DIRECTION_ARROW:
            prev_player_coord = (int(prev_player[0]), int(prev_player[1]))
            curr_player_coord = (int(curr_player[0]), int(curr_player[1]))
            cv2.circle(frame, prev_player_coord, 1, (225, 225, 0), -1)
            cv2.circle(frame, curr_player_coord, 1, (0, 255, 0), -1)

        angle = math.atan2(-(curr_player[1] - prev_player[1]), curr_player[0] - prev_player[0])
        angle_degrees = math.degrees(angle)
        if angle_degrees < 0:
            angle_degrees += 360

        angles.append(angle_degrees)

    return curr_players_center_points, angles, frames_counter
