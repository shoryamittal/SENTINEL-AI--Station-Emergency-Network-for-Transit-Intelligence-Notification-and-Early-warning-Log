# Hackathon Guide

## Demo Checklist
Before presenting, make sure you have:
- [ ] Installed all dependencies (`pip install -r requirements.txt`)
- [ ] Verified system installation (`python verify_system.py`)
- [ ] Run the test suite (`python COMPREHENSIVE_TEST.py`)
- [ ] Tested the Streamlit dashboard (`streamlit run app.py`)
- [ ] Tested the CLI version (`python main.py`)
- [ ] Prepared sample videos (if no webcam available)
- [ ] Checked that all README links are working
- [ ] Updated any team member names/contacts (if needed)

---

## Presentation Structure
A typical 5-7 minute hackathon presentation:

### 1. Problem Statement (30-60 seconds)
- Show a real-world example of crowd disasters at railway stations
- Highlight the pain points: reactive systems, no railway integration, etc.

### 2. Solution Overview (30-60 seconds)
- Introduce Suraksha Kavach AI
- Highlight key features: real-time monitoring, prediction, railway integration

### 3. Technical Depth (1-2 minutes)
- Briefly explain the tech stack (YOLOv8, LSTM, Streamlit, etc.)
- Show the system architecture (see `docs/ARCHITECTURE.txt`)

### 4. Live Demo (2-3 minutes)
- Use the Streamlit dashboard for the best visual impact
- Show:
  - Live feed with detections and heatmaps
  - State changes (GREEN → YELLOW → RED)
  - Predictions and recommendations
  - Railway integration

### 5. Impact & Future Work (30-60 seconds)
- Explain how this can save lives
- Mention future improvements (edge deployment, multi-camera, etc.)

---

## Demo Tips
1. Use pre-recorded videos if the internet is unstable or you don't have a camera feed available
2. Practice the demo multiple times to ensure smooth delivery
3. Have a backup plan (e.g., screenshots, pre-run test results) in case the live demo fails
4. Keep the demo focused – show the most impressive features first
5. Explain what's happening as you go – don't assume the judges know your system

---

## Key Files for Judges
Make sure these files are easy to find:
1. `README.md` - Comprehensive project overview
2. `app.py` - Streamlit web dashboard
3. `main.py` - CLI entry point
4. `requirements.txt` - Dependencies
5. `COMPREHENSIVE_TEST.py` - Test suite
6. `verify_system.py` - System verification

---

Good luck at the hackathon!
