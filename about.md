# GD Clipper

Companion mod for the **GD Clipper** recorder app.

The mod itself records nothing. It only tells the app (over a local named pipe, `\\.\pipe\gdclipper`) when you enter a level, start an attempt, die, pause, complete or quit. The app keeps a rolling screen recording and, on a clear, cuts out the winning attempt and saves it as mp4.

Start the app and the game in any order.
