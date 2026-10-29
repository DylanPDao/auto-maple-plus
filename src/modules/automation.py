import pyautogui
import cv2
import src.common.config as config, src.common.utils as utils

# The revive dialog's OK button
REVIVE_TEMPLATE = cv2.imread('assets/revive.png', 0)

def autoRevive():
    frame = config.capture.frame #entire screen
    okButtonPos = utils.multi_match(frame, REVIVE_TEMPLATE, 0.8)[0]
    pyautogui.click(x=okButtonPos[0]+config.capture.window["left"],y=okButtonPos[1]+config.capture.window["top"])
    pyautogui.move(0,50)
