# 🚀 Deployment Guide

## Option 1: Local Deployment (Easiest!)
Just run one of these:
- Streamlit: `python -m streamlit run app.py`
- Flask: `python deploy.py` (without ngrok
- Main CLI: `python main.py`

## Option 2: Public Deployment with ngrok

### Step 1: Create ngrok Account
1. Go to https://ngrok.com/signup
2. Sign up for a free account
3. Get your authtoken from https://dashboard.ngrok.com/get-started/your-authtoken

### Step 2: Configure ngrok
Run this command in your terminal:
```bash
ngrok config add-authtoken YOUR_AUTH_TOKEN
```

### Step 3: Run Deploy Server
```bash
python deploy.py
```

Your public URL will be printed on the console!

## Option 3: Hackathon Presentation Tips
For best hackathon experience:
1. Use Streamlit app: `python -m streamlit run app.py`
2. Share your screen with the live feed
3. Or run deploy.py locally and use ngrok for a public link
