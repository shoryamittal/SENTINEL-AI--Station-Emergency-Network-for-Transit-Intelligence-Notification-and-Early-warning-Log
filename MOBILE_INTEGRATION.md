# 📱 Mobile Phone Camera Integration Guide
Use any smartphone's camera with Suraksha Kavach AI!

## 🚀 Quick Start
### Step 1: Get your local IP address
On Windows, open PowerShell and run:
```powershell
ipconfig
```
Look for "IPv4 Address" under your Wi-Fi adapter (e.g., 192.168.1.100)

### Step 2: Start the mobile server
```bash
python deploy_mobile.py
```

### Step 3: Connect your phone
1. Make sure your phone is on the **same Wi-Fi network** as your computer
2. Open your phone's browser
3. Navigate to: `http://[your-local-ip]:5000`
   - Example: `http://192.168.1.100:5000`
4. Allow camera access when prompted
5. Tap "Start Camera"!

## 📋 Features
- **Phone Camera Integration**: Uses your phone's camera directly in the browser
- **Mobile-Friendly UI**: Responsive design for small screens
- **Real-time Processing**: AI runs on your computer, streams results to your phone
- **Back Camera Preference**: Auto-uses rear camera on phones

## 🔄 Alternative Methods
### Method 1: Use Ngrok for Public Access (Any Network)
1. Sign up at https://ngrok.com
2. Get your auth token
3. Start ngrok: `ngrok http 5000`
4. Share the generated HTTPS URL with anyone!

### Method 2: Stream from an IP Camera
If you have an IP camera, modify `main.py` to use your camera's RTSP URL.

## 📱 Demo Tips for Hackathons
1. **Test beforehand**: Ensure your phone and computer are on the same network
2. **Show both**: Use your phone to capture video, and your computer to show the dashboard
3. **Use a tripod**: If possible, stabilize your phone for better video

## ✅ Troubleshooting
- **Camera not working?** Check browser permissions and ensure you're on HTTPS or localhost
- **Connection failed?** Verify both devices are on the same Wi-Fi
- **Slow performance?** Reduce resolution or use a wired connection
