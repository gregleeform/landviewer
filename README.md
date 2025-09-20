<div align="center">
<img width="1200" height="475" alt="GHBanner" src="https://github.com/user-attachments/assets/0aa67016-6eaf-458a-adb2-6e31a0763ed6" />
</div>

# Run and deploy your AI Studio app

This contains everything you need to run your app locally.

View your app in AI Studio: https://ai.studio/apps/drive/11PQyrPZPjrgqLpjauM5lbUagr6I_9fco

## Run Locally

**Prerequisites:**  Node.js


1. Install dependencies:
   `npm install`
2. Set the `GEMINI_API_KEY` in [.env.local](.env.local) to your Gemini API key
3. Run the app:
   `npm run dev`

## Python Desktop Prototype

The `python_app/` directory contains an in-progress PySide6 port of the Landviewer app.
The current prototype focuses on reproducing the upload workflow with placeholders for the
subsequent crop and editor stages.

### Run the desktop prototype

**Prerequisites:** Python 3.10+

1. `cd python_app`
2. (Optional) create a virtual environment: `python -m venv .venv` and activate it
3. Install dependencies: `pip install -r requirements.txt`
4. Launch the app: `python main.py` *(or `python -m landviewer_desktop`)*

The window will allow you to choose the cadastral and field images, mirroring the web
application's first step while we iterate on the remaining features.
