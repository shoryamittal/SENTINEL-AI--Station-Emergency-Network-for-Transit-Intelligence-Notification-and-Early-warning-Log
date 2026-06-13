#!/usr/bin/env python3
"""
SENTINEL AI - Professional Deployment
Architected for 90-Second Intervention Window
"""
import os
import cv2
import time
import logging
import numpy as np
import base64
from pathlib import Path
from threading import Lock
from flask import Flask, render_template_string, Response, jsonify, request

# Add src to path
import sys
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src import (
    CrowdDensityAnalyzer,
    OccupancyMapper,
    DensityPredictor,
    SituationClassifier,
    ActionExecutor
)

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

@app.after_request
def add_cache_control(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

# Global state (thread-safe with locks)
frame_lock = Lock()
log_lock = Lock()
status_lock = Lock()

# Global variables
current_frames = {}  # camera_id -> frame
current_info = {}
system_running = False
mode = "simulation"  # "camera" or "simulation" or "uploaded_video"
monitor = None
live_logs = []
team_alerts = []
max_logs = 50

# Simulation state
simulation_zone = "GREEN"
uploaded_video_path = None
# Video capture objects
camera_cap = None  # For live camera
video_cap = None   # For uploaded video

# Zone-specific action recommendations from architecture diagram
ZONE_ACTIONS = {
    "GREEN": {
        "title": "Normal Monitoring",
        "actions": [
            "Continue monitoring crowd movement",
            "Track density trends",
            "Watch for transition to YELLOW zone"
        ]
    },
    "YELLOW": {
        "title": "Increased Monitoring",
        "actions": [
            "Compute shortest alternate paths",
            "Identify crowd diversion routes",
            "Prepare digital display updates",
            "Pre-position RPF staff at key points"
        ]
    },
    "RED": {
        "title": "High Risk Situation",
        "actions": [
            "Activate dynamic route recommendations",
            "Update all passenger information displays",
            "Deploy RPF personnel to critical zones",
            "Activate alternate access routes"
        ]
    },
    "BLACK": {
        "title": "Critical Emergency",
        "actions": [
            "Restrict inflow at entry gates/staircases",
            "Hold automatic announcements",
            "Escalate to Station Control & RPF Command",
            "Initiate emergency crowd management protocols"
        ]
    }
}

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SENTINEL AI - Crowd Monitoring System</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
            background: #0f172a;
            color: #e2e8f0;
            min-height: 100vh;
        }
        /* Header */
        .header {
            background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
            border-bottom: 1px solid #334155;
            padding: 1.5rem 2rem;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
        }
        .header-content {
            max-width: 2000px;
            margin: 0 auto;
            display: flex;
            flex-direction: row;
            align-items: center;
            justify-content: space-between;
        }
        .header-left {
            display: flex;
            flex-direction: column;
            gap: 0.25rem;
        }
        .header h1 {
            font-size: 1.75rem;
            font-weight: 700;
            letter-spacing: 0.02em;
            color: #34d399;
        }
        .subtitle {
            font-size: 0.875rem;
            font-weight: 500;
            color: #94a3b8;
            letter-spacing: 0.01em;
        }
        .status-badge {
            padding: 0.5rem 1rem;
            background: #064e3b;
            border: 1px solid #065f46;
            border-radius: 0.5rem;
            font-weight: 600;
            color: #6ee7b7;
        }
        /* Container */
        .container {
            max-width: 2000px;
            margin: 0 auto;
            padding: 1.5rem 2rem;
            display: grid;
            grid-template-columns: 1fr 350px;
            gap: 1.5rem;
        }
        /* Left Column */
        .main-panel {
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
        }
        /* Stats Grid */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 1rem;
        }
        .stat-card {
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 0.75rem;
            padding: 1.25rem;
            transition: all 0.2s;
        }
        .stat-card:hover {
            border-color: #475569;
        }
        .stat-label {
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #94a3b8;
            font-weight: 600;
            margin-bottom: 0.5rem;
        }
        .stat-value {
            font-size: 1.75rem;
            font-weight: 700;
            color: #f1f5f9;
        }
        /* Video Section */
        .video-section {
            background: #1e293b;
            border-radius: 0.75rem;
            border: 1px solid #334155;
            overflow: hidden;
        }
        .video-header {
            padding: 1rem 1.5rem;
            border-bottom: 1px solid #334155;
            background: #1a2332;
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 0.75rem;
        }
        .video-title {
            font-weight: 600;
            font-size: 1rem;
            color: #cbd5e1;
        }
        .video-controls {
            display: flex;
            gap: 0.5rem;
            flex-wrap: wrap;
        }
        .control-btn {
            padding: 0.5rem 1rem;
            border: none;
            border-radius: 0.375rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            font-size: 0.875rem;
        }
        .control-btn.primary {
            background: #10b981;
            color: white;
        }
        .control-btn.primary:hover {
            background: #059669;
        }
        .control-btn.secondary {
            background: #334155;
            color: #cbd5e1;
        }
        .control-btn.secondary:hover {
            background: #475569;
        }
        .upload-container {
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        .upload-btn {
            padding: 0.5rem 1rem;
            background: #3b82f6;
            color: white;
            border: none;
            border-radius: 0.375rem;
            font-weight: 600;
            cursor: pointer;
        }
        .upload-btn:hover {
            background: #2563eb;
        }
        .video-container {
            aspect-ratio: 16/9;
            background: #000;
            position: relative;
        }
        .video-container img {
            width: 100%;
            height: 100%;
            object-fit: contain;
        }
        .video-overlay {
            position: absolute;
            top: 1rem;
            left: 1rem;
            background: rgba(15, 23, 42, 0.9);
            padding: 0.75rem 1rem;
            border-radius: 0.5rem;
            border: 1px solid #334155;
        }
        /* Right Column */
        .side-panel {
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
        }
        /* Zone Indicator */
        .zone-card {
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 0.75rem;
            padding: 1.5rem;
        }
        .zone-title {
            font-size: 0.875rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #94a3b8;
            margin-bottom: 1rem;
        }
        .zone-display {
            text-align: center;
            padding: 1.5rem;
            border-radius: 0.5rem;
            background: rgba(16, 185, 129, 0.1);
            border: 1px solid #065f46;
            margin-bottom: 1rem;
        }
        .zone-display.yellow {
            background: rgba(245, 158, 11, 0.1);
            border-color: #92400e;
        }
        .zone-display.red {
            background: rgba(239, 68, 68, 0.1);
            border-color: #991b1b;
        }
        .zone-display.black {
            background: rgba(71, 85, 105, 0.1);
            border-color: #334155;
        }
        .zone-name {
            font-size: 2rem;
            font-weight: 800;
            color: #10b981;
        }
        .zone-display.yellow .zone-name {
            color: #f59e0b;
        }
        .zone-display.red .zone-name {
            color: #ef4444;
        }
        .zone-display.black .zone-name {
            color: #94a3b8;
        }
        .progress-section {
            margin-top: 1rem;
        }
        .progress-label {
            display: flex;
            justify-content: space-between;
            font-size: 0.75rem;
            color: #94a3b8;
            margin-bottom: 0.5rem;
        }
        .progress-bar {
            height: 8px;
            background: #334155;
            border-radius: 4px;
            overflow: hidden;
        }
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #10b981, #059669);
            transition: width 0.3s;
        }
        .zone-display.yellow .progress-fill {
            background: linear-gradient(90deg, #f59e0b, #d97706);
        }
        .zone-display.red .progress-fill {
            background: linear-gradient(90deg, #ef4444, #dc2626);
        }
        .zone-display.black .progress-fill {
            background: linear-gradient(90deg, #64748b, #475569);
        }
        /* Simulation Controls */
        .simulation-card {
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 0.75rem;
            padding: 1.5rem;
        }
        .simulation-title {
            font-weight: 600;
            font-size: 0.875rem;
            color: #cbd5e1;
            margin-bottom: 1rem;
        }
        .simulation-buttons {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 0.5rem;
            margin-bottom: 1rem;
        }
        .sim-btn {
            padding: 0.75rem;
            border: 1px solid #334155;
            border-radius: 0.5rem;
            background: #1a2332;
            color: #cbd5e1;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }
        .sim-btn:hover {
            background: #334155;
        }
        .sim-btn.green:hover, .sim-btn.green.active {
            background: rgba(16, 185, 129, 0.2);
            border-color: #065f46;
            color: #10b981;
        }
        .sim-btn.yellow:hover, .sim-btn.yellow.active {
            background: rgba(245, 158, 11, 0.2);
            border-color: #92400e;
            color: #f59e0b;
        }
        .sim-btn.red:hover, .sim-btn.red.active {
            background: rgba(239, 68, 68, 0.2);
            border-color: #991b1b;
            color: #ef4444;
        }
        .sim-btn.black:hover, .sim-btn.black.active {
            background: rgba(71, 85, 105, 0.2);
            border-color: #334155;
            color: #94a3b8;
        }
        /* Action Recommendations */
        .actions-card {
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 0.75rem;
            padding: 1.5rem;
        }
        .actions-title {
            font-weight: 600;
            font-size: 0.875rem;
            color: #cbd5e1;
            margin-bottom: 1rem;
        }
        .action-list {
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
        }
        .action-item {
            background: #1a2332;
            padding: 0.75rem;
            border-radius: 0.375rem;
            border-left: 3px solid #10b981;
        }
        .action-item.yellow {
            border-left-color: #f59e0b;
        }
        .action-item.red {
            border-left-color: #ef4444;
        }
        .action-item.black {
            border-left-color: #64748b;
        }
        /* Logs */
        .logs-card {
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 0.75rem;
            padding: 1.5rem;
            flex: 1;
        }
        .logs-title {
            font-weight: 600;
            font-size: 0.875rem;
            color: #cbd5e1;
            margin-bottom: 1rem;
        }
        .logs-container {
            height: 250px;
            overflow-y: auto;
            background: #0f172a;
            border-radius: 0.5rem;
            padding: 1rem;
            border: 1px solid #334155;
        }
        .log-entry {
            padding: 0.75rem;
            margin-bottom: 0.5rem;
            background: #1a2332;
            border-radius: 0.375rem;
            border-left: 3px solid #10b981;
        }
        .log-entry.yellow {
            border-left-color: #f59e0b;
        }
        .log-entry.red {
            border-left-color: #ef4444;
        }
        .log-entry.black {
            border-left-color: #64748b;
        }
        .log-time {
            font-size: 0.75rem;
            color: #64748b;
            font-family: 'Courier New', monospace;
            margin-bottom: 0.25rem;
        }
        .log-message {
            font-size: 0.875rem;
            color: #e2e8f0;
        }
        /* Team Alerts */
        .alerts-card {
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 0.75rem;
            padding: 1.5rem;
        }
        .alerts-title {
            font-weight: 600;
            font-size: 0.875rem;
            color: #cbd5e1;
            margin-bottom: 1rem;
        }
        .alert-item {
            padding: 1rem;
            background: #1a2332;
            border-radius: 0.5rem;
            margin-bottom: 0.75rem;
            border-left: 3px solid #10b981;
        }
        .alert-item.warning {
            border-left-color: #f59e0b;
        }
        .alert-item.critical {
            border-left-color: #ef4444;
        }
        .alert-title {
            font-weight: 600;
            font-size: 0.875rem;
            margin-bottom: 0.25rem;
        }
        .alert-desc {
            font-size: 0.75rem;
            color: #94a3b8;
        }
        /* Responsive */
        @media (max-width: 1200px) {
            .container {
                grid-template-columns: 1fr;
            }
            .stats-grid {
                grid-template-columns: repeat(2, 1fr);
            }
        }
        @media (max-width: 768px) {
            .stats-grid {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
    <div class="header">
        <div class="header-content">
            <div class="header-left">
                <h1>SENTINEL AI</h1>
                <div class="subtitle">Station Emergency Network for Transit Intelligence, Notification and Early-warning Logic</div>
            </div>
            <div class="status-badge">
                <span id="system-status">System Active</span>
            </div>
        </div>
    </div>
    
    <div class="container">
        <!-- Main Panel -->
        <div class="main-panel">
            <!-- Stats Grid -->
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-label">People Count</div>
                    <div class="stat-value" id="people-count">0</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Max Density (p/m²)</div>
                    <div class="stat-value" id="max-density">0.00</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Avg Density (p/m²)</div>
                    <div class="stat-value" id="avg-density">0.00</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Predicted Density</div>
                    <div class="stat-value" id="predicted-density">0.00</div>
                </div>
            </div>
            
            <!-- Video Section -->
            <div class="video-section">
                <div class="video-header">
                    <div class="video-title" id="video-title">Live Camera Feed - Camera 1</div>
                    <div class="video-controls">
                        <div class="upload-container">
                            <input type="file" id="video-upload" accept="video/*" style="display: none;">
                            <button class="upload-btn" onclick="document.getElementById('video-upload').click()">Upload Video</button>
                        </div>
                        <button class="control-btn secondary" id="sim-btn" onclick="switchMode('simulation')">Simulation Mode</button>
                        <button class="control-btn secondary" id="webcam-btn" onclick="toggleWebCam()">Web Camera</button>
                        <button class="control-btn secondary" id="camera-btn" onclick="switchMode('camera')">Server Camera</button>
                    </div>
                </div>
                <div class="video-container">
                    <div class="video-overlay">
                        <div id="overlay-text">Monitoring Active</div>
                    </div>
                    <video id="webcam-feed" autoplay playsinline style="display: none; width: 100%; height: 100%; object-fit: contain;"></video>
                    <canvas id="webcam-canvas" style="display: none;"></canvas>
                    <img id="video-feed" src="" alt="Live Feed">
                </div>
            </div>
            
            <!-- Action Recommendations -->
            <div class="actions-card">
                <div class="actions-title" id="actions-title">Recommended Actions</div>
                <div class="action-list" id="action-list">
                    <!-- Actions will be added here -->
                </div>
            </div>
            
            <!-- Team Alerts -->
            <div class="alerts-card">
                <div class="alerts-title">Team Status & Alerts</div>
                <div id="team-alerts">
                    <div class="alert-item">
                        <div class="alert-title">System Initialized</div>
                        <div class="alert-desc">All components operational</div>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Side Panel -->
        <div class="side-panel">
            <!-- Zone Indicator -->
            <div class="zone-card">
                <div class="zone-title">Current Zone</div>
                <div class="zone-display" id="zone-display">
                    <div class="zone-name" id="zone-name">GREEN</div>
                </div>
                <div class="progress-section">
                    <div class="progress-label">
                        <span>Transition Progress</span>
                        <span id="transition-percent">0%</span>
                    </div>
                    <div class="progress-bar">
                        <div class="progress-fill" id="transition-fill" style="width: 0%"></div>
                    </div>
                </div>
            </div>
            
            <!-- Simulation Controls -->
            <div class="simulation-card" id="simulation-panel">
                <div class="simulation-title">Simulation Controls</div>
                <div class="simulation-buttons">
                    <button class="sim-btn green active" onclick="setSimulationZone('GREEN')">GREEN</button>
                    <button class="sim-btn yellow" onclick="setSimulationZone('YELLOW')">YELLOW</button>
                    <button class="sim-btn red" onclick="setSimulationZone('RED')">RED</button>
                    <button class="sim-btn black" onclick="setSimulationZone('BLACK')">BLACK</button>
                </div>
            </div>
            
            <!-- Logs -->
            <div class="logs-card">
                <div class="logs-title">Event Logs</div>
                <div class="logs-container" id="logs-container">
                    <!-- Logs will be added here -->
                </div>
            </div>
        </div>
    </div>
    
    <script>
        let currentMode = 'simulation';
        let currentZone = 'GREEN';
        const zoneActions = {
            'GREEN': {
                'title': 'Normal Monitoring Actions',
                'actions': [
                    'Continue monitoring crowd movement',
                    'Track density trends',
                    'Watch for transition to YELLOW zone'
                ]
            },
            'YELLOW': {
                'title': 'Increased Monitoring Actions',
                'actions': [
                    'Compute shortest alternate paths',
                    'Identify crowd diversion routes',
                    'Prepare digital display updates',
                    'Pre-position RPF staff at key points'
                ]
            },
            'RED': {
                'title': 'High Risk Situation Actions',
                'actions': [
                    'Activate dynamic route recommendations',
                    'Update all passenger information displays',
                    'Deploy RPF personnel to critical zones',
                    'Activate alternate access routes'
                ]
            },
            'BLACK': {
                'title': 'Critical Emergency Actions',
                'actions': [
                    'Restrict inflow at entry gates/staircases',
                    'Hold automatic announcements',
                    'Escalate to Station Control & RPF Command',
                    'Initiate emergency crowd management protocols'
                ]
            }
        };
        
        // Webcam variables
        let webcamActive = false;
        let webcamStream = null;
        let webcamAnimationId = null;
        let webcamVideo = null;
        let webcamCanvas = null;
        let webcamCtx = null;
        
        // Initialize webcam elements on load
        window.addEventListener('DOMContentLoaded', () => {
            webcamVideo = document.getElementById('webcam-feed');
            webcamCanvas = document.getElementById('webcam-canvas');
            webcamCtx = webcamCanvas.getContext('2d');
        });
        
        async function toggleWebCam() {
            if (webcamActive) {
                stopWebCam();
            } else {
                await startWebCam();
            }
        }
        
        let videoFeedInitialized = false;
        
        async function startWebCam() {
            try {
                webcamStream = await navigator.mediaDevices.getUserMedia({
                    video: {
                        facingMode: 'environment',
                        width: { ideal: 1280 },
                        height: { ideal: 720 }
                    },
                    audio: false
                });
                webcamVideo.srcObject = webcamStream;
                webcamVideo.style.display = 'block';
                document.getElementById('video-feed').style.display = 'none';
                
                // Update button
                document.getElementById('webcam-btn').classList.remove('secondary');
                document.getElementById('webcam-btn').classList.add('primary');
                document.getElementById('webcam-btn').textContent = 'Stop Webcam';
                
                webcamActive = true;
                document.getElementById('video-title').textContent = 'Web Camera Feed';
                
                // Start processing loop
                startWebCamProcessing();
            } catch (e) {
                console.error('Error accessing webcam:', e);
                alert('Could not access webcam: ' + e.message);
            }
        }
        
        function stopWebCam() {
            if (webcamStream) {
                webcamStream.getTracks().forEach(track => track.stop());
            }
            if (webcamAnimationId) {
                cancelAnimationFrame(webcamAnimationId);
            }
            
            webcamVideo.style.display = 'none';
            const videoFeed = document.getElementById('video-feed');
            videoFeed.style.display = 'block';
            if (!videoFeed.src.includes('video_feed')) {
                videoFeed.src = '/video_feed?cam=0&t=' + Date.now();
            }
            
            document.getElementById('webcam-btn').classList.remove('primary');
            document.getElementById('webcam-btn').classList.add('secondary');
            document.getElementById('webcam-btn').textContent = 'Web Camera';
            
            webcamActive = false;
        }
        
        async function startWebCamProcessing() {
            if (!webcamActive) return;
            
            // Resize canvas
            webcamCanvas.width = webcamVideo.videoWidth || 640;
            webcamCanvas.height = webcamVideo.videoHeight || 480;
            
            webcamCtx.drawImage(webcamVideo, 0, 0, webcamCanvas.width, webcamCanvas.height);
            
            // Convert to base64
            const imageData = webcamCanvas.toDataURL('image/jpeg', 0.6);
            
            try {
                const response = await fetch('/process_frame', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ image: imageData })
                });
            } catch (e) {
                console.error('Processing error:', e);
            }
            
            webcamAnimationId = requestAnimationFrame(startWebCamProcessing);
        }
        
        function switchMode(mode) {
            currentMode = mode;
            document.getElementById('camera-btn').classList.toggle('primary', mode === 'camera');
            document.getElementById('camera-btn').classList.toggle('secondary', mode !== 'camera');
            document.getElementById('sim-btn').classList.toggle('primary', mode === 'simulation');
            document.getElementById('sim-btn').classList.toggle('secondary', mode !== 'simulation');
            document.getElementById('simulation-panel').style.display = mode === 'simulation' || mode === 'uploaded_video' ? 'block' : 'none';
            document.getElementById('video-title').textContent = mode === 'camera' ? 'Live Camera Feed - Camera 1' : 
                                                          mode === 'uploaded_video' ? 'Video Analysis Mode' :
                                                          'Simulation Mode';
            fetch('/switch_mode?mode=' + mode);
        }
        
        function setSimulationZone(zone) {
            currentZone = zone;
            document.querySelectorAll('.sim-btn').forEach(btn => {
                btn.classList.remove('active');
            });
            event.target.classList.add('active');
            updateActionRecommendations(zone);
            fetch('/set_simulation_zone?zone=' + zone);
        }
        
        function updateActionRecommendations(zone) {
            const actions = zoneActions[zone];
            document.getElementById('actions-title').textContent = actions.title;
            const actionList = document.getElementById('action-list');
            actionList.innerHTML = '';
            actions.actions.forEach(action => {
                const actionDiv = document.createElement('div');
                actionDiv.className = 'action-item ' + zone.toLowerCase();
                actionDiv.textContent = action;
                actionList.appendChild(actionDiv);
            });
        }
        
        // Handle video upload
        document.getElementById('video-upload').addEventListener('change', function(e) {
            const file = e.target.files[0];
            if (file) {
                const formData = new FormData();
                formData.append('video', file);
                
                fetch('/upload_video', {
                    method: 'POST',
                    body: formData
                }).then(response => response.json())
                  .then(data => {
                      if (data.success) {
                          switchMode('uploaded_video');
                      }
                  }).catch(error => {
                      console.error('Error uploading video:', error);
                  });
            }
        });
        
        async function updateUI() {
            try {
                const response = await fetch('/status');
                const data = await response.json();
                
                if (data.info) {
                    const info = data.info;
                    
                    // Update stats
                    const peopleCount = info.person_count || info.peopleCount || 0;
                    const maxDensity = info.max_density || info.maxDensity || 0;
                    const avgDensity = info.avg_density || 0;
                    const predictedDensity = info.predicted_density || info.prediction || 0;
                    document.getElementById('people-count').textContent = peopleCount;
                    document.getElementById('max-density').textContent = maxDensity.toFixed(2);
                    document.getElementById('avg-density').textContent = avgDensity.toFixed(2);
                    document.getElementById('predicted-density').textContent = predictedDensity.toFixed(2);
                    
                    // Update zone
                    const zone = (info.state || 'GREEN');
                    if (zone !== currentZone) {
                        currentZone = zone;
                        updateActionRecommendations(zone);
                        // Update simulation buttons
                        document.querySelectorAll('.sim-btn').forEach(btn => {
                            btn.classList.remove('active');
                            if (btn.textContent.trim() === zone) {
                                btn.classList.add('active');
                            }
                        });
                    }
                    const zoneDisplay = document.getElementById('zone-display');
                    const zoneName = document.getElementById('zone-name');
                    
                    zoneDisplay.className = 'zone-display ' + zone.toLowerCase();
                    zoneName.textContent = zone;
                    
                    // Update transition
                    const transitionPercent = info.transition_percent || 0;
                    document.getElementById('transition-percent').textContent = Math.round(transitionPercent) + '%';
                    document.getElementById('transition-fill').style.width = transitionPercent + '%';
                }
                
                // Update logs
                if (data.logs) {
                    const logsContainer = document.getElementById('logs-container');
                    logsContainer.innerHTML = '';
                    data.logs.forEach(log => {
                        const logDiv = document.createElement('div');
                        logDiv.className = 'log-entry ' + log.zone.toLowerCase();
                        const count = log.count || log.people || 0;
                        const time = log.timestamp || log.time || '00:00:00';
                        logDiv.innerHTML = '<div class="log-time">' + time + '</div>' +
                                          '<div class="log-message">Density: ' + log.density.toFixed(2) + ' | Count: ' + count + ' | Zone: ' + log.zone + '</div>';
                        logsContainer.appendChild(logDiv);
                    });
                }
                
                // Update team alerts
                if (data.team_alerts) {
                    const alertsContainer = document.getElementById('team-alerts');
                    alertsContainer.innerHTML = '';
                    data.team_alerts.forEach(alert => {
                        const alertDiv = document.createElement('div');
                        alertDiv.className = 'alert-item ' + alert.type;
                        alertDiv.innerHTML = '<div class="alert-title">' + alert.title + '</div>' +
                                          '<div class="alert-desc">' + alert.message + '</div>';
                        alertsContainer.appendChild(alertDiv);
                    });
                }
                
                // Update video feed only when webcam is not active and not initialized
                if (!webcamActive && !videoFeedInitialized) {
                    const videoFeed = document.getElementById('video-feed');
                    videoFeed.src = '/video_feed?cam=0&t=' + Date.now();
                    videoFeedInitialized = true;
                }
                
            } catch (e) {
                console.error('Update error:', e);
            }
        }
        
        // Initialize action recommendations
        updateActionRecommendations('GREEN');
        
        // Start monitoring and update UI
        fetch('/start');
        setInterval(updateUI, 200);
        updateUI();
    </script>
</body>
</html>
"""

def initialize_system():
    """Initialize monitoring system"""
    global monitor, current_info, camera_cap
    print("Initializing SENTINEL AI system...")
    
    config = {
        "camera_source": 0,
        "fps": 30,
        "yolo_model": "yolov8n.pt",
        "confidence_threshold": 0.5,
        "grid_rows": 4,
        "grid_cols": 6,
        "zone_area_m2": 10.0,
        "green_threshold": 4.0,
        "yellow_threshold": 5.0,
        "red_threshold": 6.0,
        "history_length": 30,
        "prediction_horizon": 1.0,
        "station_name": "Central Station"
    }
    
    print("Loading YOLO model...")
    monitor = {
        "analyzer": CrowdDensityAnalyzer(model_name=config["yolo_model"], confidence_threshold=config["confidence_threshold"]),
        "mapper": OccupancyMapper(grid_size=(config["grid_rows"], config["grid_cols"]), zone_area_m2=config["zone_area_m2"]),
        "predictor": DensityPredictor(history_length=config["history_length"], prediction_horizon=config["prediction_horizon"]),
        "classifier": SituationClassifier(green_threshold=config["green_threshold"], yellow_threshold=config["yellow_threshold"], red_threshold=config["red_threshold"]),
        "executor": ActionExecutor(station_name=config["station_name"]),
        "config": config,
        "cameras": {},
        "active_cameras": 1,
        "running": False,
        "frame_count": 0,
        "start_time": None
    }
    print("Model loaded successfully!")
    
    # Initialize camera (but don't start yet)
    try:
        camera_cap = cv2.VideoCapture(0)
        if not camera_cap.isOpened():
            print("Camera not available, using simulation as default")
            camera_cap = None
        else:
            print("Camera initialized successfully!")
    except Exception as e:
        print(f"Error initializing camera: {e}")
        camera_cap = None
    
    # Initialize current info with default values
    current_info = {
        "state": "GREEN",
        "confidence": 0.95,
        "person_count": 0,
        "max_density": 0.0,
        "avg_density": 0.0,
        "predicted_density": 0.0,
        "trend": "stable",
        "transition_percent": 0.0
    }
    
    # Initialize default frame
    default_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(default_frame, "SENTINEL AI", (180, 200), 
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (16, 185, 129), 2)
    cv2.putText(default_frame, "Monitoring Ready", (220, 250), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (16, 185, 129), 1)
    
    with frame_lock:
        current_frames[0] = default_frame
    
    add_team_alert("System Initialized", "All components operational", "info")
    
    return True

def add_log(timestamp, density, count, zone):
    """Add log entry"""
    global live_logs
    with log_lock:
        live_logs.insert(0, {
            "timestamp": timestamp,
            "density": density,
            "count": count,
            "zone": zone
        })
        if len(live_logs) > max_logs:
            live_logs.pop()

def add_team_alert(title, message, alert_type="info"):
    """Add team status alert - prevent duplicates"""
    global team_alerts
    with status_lock:
        # Check if this alert is already the most recent
        if team_alerts and team_alerts[0]["title"] == title and team_alerts[0]["message"] == message:
            return
        team_alerts.insert(0, {
            "title": title,
            "message": message,
            "type": alert_type
        })
        if len(team_alerts) > 10:
            team_alerts.pop()

def calculate_transition_percentage(density, zone, thresholds):
    """Calculate transition percentage to next zone"""
    green_thresh = thresholds["green"]
    yellow_thresh = thresholds["yellow"]
    red_thresh = thresholds["red"]
    
    if zone == "GREEN":
        if density < green_thresh:
            return (density / green_thresh) * 100
        return 100
    elif zone == "YELLOW":
        range_size = yellow_thresh - green_thresh
        if density < yellow_thresh:
            return ((density - green_thresh) / range_size) * 100
        return 100
    elif zone == "RED":
        range_size = red_thresh - yellow_thresh
        if density < red_thresh:
            return ((density - yellow_thresh) / range_size) * 100
        return 100
    else:  # BLACK
        return 100

def generate_frames():
    """Video streaming generator function"""
    global current_frames, system_running, monitor, mode, simulation_zone, uploaded_video_path, video_cap
    
    default_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(default_frame, "SENTINEL AI", (180, 200), 
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (16, 185, 129), 2)
    cv2.putText(default_frame, "Monitoring Ready", (220, 250), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (16, 185, 129), 1)
    
    while True:
        time.sleep(0.05)
        try:
            with frame_lock:
                current_frame = current_frames.get(0, default_frame)
                _, buffer = cv2.imencode('.jpg', current_frame)
                frame = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        except Exception as e:
            print(f"Error in generate_frames: {e}")
            time.sleep(0.5)

@app.route('/')
def index():
    return HTML_TEMPLATE

@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/switch_mode')
def switch_mode():
    global mode, previous_mode
    new_mode = request.args.get('mode', 'simulation')
    if new_mode != previous_mode:
        mode = new_mode
        previous_mode = new_mode
        
        if mode == "camera":
            if camera_cap is not None and camera_cap.isOpened():
                add_team_alert("Switched to Camera Mode", "Now monitoring from live camera feed", "info")
            else:
                mode = "simulation"
                previous_mode = "simulation"
                add_team_alert("Camera Not Available", "No camera detected. Switched to simulation mode.", "warning")
        elif mode == "uploaded_video":
            if uploaded_video_path:
                add_team_alert("Switched to Video Analysis Mode", "Now analyzing uploaded video", "info")
            else:
                mode = "simulation"
                previous_mode = "simulation"
                add_team_alert("No Video Uploaded", "Please upload a video first. Switched to simulation mode.", "warning")
        else:
            add_team_alert("Switched to Simulation Mode", "Now running in simulation mode", "info")
        
    return jsonify({"success": True})

# Track previous zone to prevent duplicate alerts
previous_simulation_zone = "GREEN"
# Track previous mode to prevent duplicate alerts
previous_mode = "simulation"

@app.route('/set_simulation_zone')
def set_simulation_zone():
    global simulation_zone, previous_simulation_zone
    new_zone = request.args.get('zone', 'GREEN')
    
    if new_zone != previous_simulation_zone:
        simulation_zone = new_zone
        previous_simulation_zone = new_zone
        
        # Add appropriate alerts based on zone
        if simulation_zone == "GREEN":
            add_team_alert("All Clear", "Crowd density at safe levels. Normal operations continue.", "info")
        elif simulation_zone == "YELLOW":
            add_team_alert("Monitoring Increased", "Crowd density rising. RPF teams on standby.", "warning")
        elif simulation_zone == "RED":
            add_team_alert("Critical Situation", "High crowd density detected. RPF deployment initiated.", "critical")
        else:
            add_team_alert("Emergency Protocol", "Extreme density. Full emergency response activated.", "critical")
    
    return jsonify({"success": True})

@app.route('/upload_video', methods=['POST'])
def upload_video():
    global uploaded_video_path, video_cap, mode, previous_mode
    try:
        if 'video' not in request.files:
            return jsonify({"success": False, "error": "No video file"})
        
        file = request.files['video']
        if file.filename == '':
            return jsonify({"success": False, "error": "No selected file"})
        
        # Save uploaded video
        upload_dir = Path("uploads")
        upload_dir.mkdir(exist_ok=True)
        video_path = upload_dir / file.filename
        file.save(str(video_path))
        
        uploaded_video_path = str(video_path)
        
        # Initialize video capture
        if video_cap is not None:
            video_cap.release()
        video_cap = cv2.VideoCapture(uploaded_video_path)
        
        add_team_alert("Video Uploaded", "Video uploaded successfully. Starting analysis.", "info")
        
        return jsonify({"success": True})
    except Exception as e:
        print(f"Error uploading video: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route('/start')
def start():
    global system_running, monitor, current_info, mode
    try:
        if not monitor:
            initialize_system()
        
        if system_running:
            return jsonify({"success": True})
        
        # Start monitoring
        monitor["running"] = True
        monitor["start_time"] = time.time()
        system_running = True
        
        if mode == "camera" and camera_cap and camera_cap.isOpened():
            add_team_alert("Monitoring Started", "Live camera feed active. Analysis in progress.", "info")
        elif mode == "uploaded_video" and uploaded_video_path:
            add_team_alert("Video Analysis Started", "Processing uploaded video.", "info")
        else:
            add_team_alert("Simulation Started", "Simulation mode active", "info")
        
        import threading
        def process_frames():
            global current_frames, current_info, system_running, monitor, mode, simulation_zone
            while system_running:
                try:
                    # Get frame based on mode
                    frame = None
                    if mode == "camera":
                        if camera_cap and camera_cap.isOpened():
                            ret, frame = camera_cap.read()
                            if not ret or frame is None:
                                time.sleep(0.05)
                                continue
                    elif mode == "uploaded_video" and uploaded_video_path:
                        if video_cap is None or not video_cap.isOpened():
                            video_cap = cv2.VideoCapture(uploaded_video_path)
                        ret, frame = video_cap.read()
                        if not ret or frame is None:
                            video_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                            continue
                    
                    # If no valid frame from camera/video, use simulation
                    if frame is None:
                        frame = np.zeros((480, 640, 3), dtype=np.uint8)
                        if simulation_zone == "GREEN":
                            frame[:] = (34, 185, 129)
                        elif simulation_zone == "YELLOW":
                            frame[:] = (11, 158, 245)
                        elif simulation_zone == "RED":
                            frame[:] = (68, 68, 239)
                        else:
                            frame[:] = (105, 85, 71)
                        cv2.putText(frame, f"SIMULATION: {simulation_zone} ZONE", (120, 240), 
                                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3)
                    
                    # Process frame
                    if (mode == "camera" and camera_cap and camera_cap.isOpened()) or (mode == "uploaded_video" and uploaded_video_path):
                        detections = monitor["analyzer"].detect_people(frame)
                        grid = monitor["mapper"].create_grid(frame.shape)
                        grid = monitor["mapper"].map_detections_to_grid(grid, detections)
                        density_grid, statistics = monitor["mapper"].calculate_density(grid)
                        monitor["predictor"].update_history(statistics)
                        prediction = monitor["predictor"].predict_future_density(time_minutes=5.0)
                        state, confidence = monitor["classifier"].classify(statistics["max_density"], prediction["trend"], prediction)
                        vis_frame = monitor["analyzer"].visualize_detections(frame, detections)
                    else:
                        statistics = {"total_people": 0, "max_density": 0.0, "avg_density": 0.0}
                        prediction = {"trend": "stable", "predicted_max_density": 0.0}
                        state = simulation_zone
                        confidence = 0.95
                        
                        if simulation_zone == "GREEN":
                            statistics["total_people"] = 45
                            statistics["max_density"] = 2.8
                            statistics["avg_density"] = 1.8
                            prediction["predicted_max_density"] = 3.1
                        elif simulation_zone == "YELLOW":
                            statistics["total_people"] = 85
                            statistics["max_density"] = 4.6
                            statistics["avg_density"] = 3.2
                            prediction["predicted_max_density"] = 5.1
                        elif simulation_zone == "RED":
                            statistics["total_people"] = 130
                            statistics["max_density"] = 5.8
                            statistics["avg_density"] = 4.5
                            prediction["predicted_max_density"] = 6.3
                        else:
                            statistics["total_people"] = 180
                            statistics["max_density"] = 7.2
                            statistics["avg_density"] = 5.8
                            prediction["predicted_max_density"] = 7.8
                        
                        vis_frame = frame
                    
                    # Determine zone color
                    color = (16, 185, 129)
                    if state == "YELLOW":
                        color = (245, 158, 11)
                    elif state == "RED":
                        color = (239, 68, 68)
                    elif state == "BLACK":
                        color = (71, 85, 105)
                    
                    # Overlay zone info on video for real camera/video
                    if (mode == "camera" and camera_cap and camera_cap.isOpened()) or (mode == "uploaded_video" and uploaded_video_path):
                        overlay = vis_frame.copy()
                        cv2.rectangle(overlay, (10, 10), (300, 130), (0, 0, 0), -1)
                        cv2.addWeighted(overlay, 0.7, vis_frame, 0.3, 0, vis_frame)
                        
                        cv2.putText(vis_frame, f"STATE: {state}", (20, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)
                        cv2.putText(vis_frame, f"People: {statistics['total_people']}", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                        cv2.putText(vis_frame, f"Density: {statistics['max_density']:.2f}", (20, 115), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                    
                    thresholds = {
                        "green": monitor["config"]["green_threshold"],
                        "yellow": monitor["config"]["yellow_threshold"],
                        "red": monitor["config"]["red_threshold"]
                    }
                    transition_percent = calculate_transition_percentage(statistics["max_density"], state, thresholds)
                    
                    with frame_lock:
                        current_frames[0] = vis_frame
                    
                    with status_lock:
                        current_info = {
                            "state": state,
                            "confidence": confidence,
                            "person_count": statistics["total_people"],
                            "max_density": statistics["max_density"],
                            "avg_density": statistics["avg_density"],
                            "predicted_density": prediction["predicted_max_density"],
                            "trend": prediction["trend"],
                            "transition_percent": transition_percent
                        }
                    
                    if monitor["frame_count"] % 30 == 0:
                        timestamp = time.strftime("%H:%M:%S")
                        add_log(timestamp, statistics["max_density"], statistics["total_people"], state)
                    
                    monitor["frame_count"] += 1
                    time.sleep(0.033)
                except Exception as e:
                    print(f"Error in process_frames: {e}")
                    time.sleep(0.1)
        
        threading.Thread(target=process_frames, daemon=True).start()
        return jsonify({"success": True})
    except Exception as e:
        print(f"Start error: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route('/process_frame', methods=['POST'])
def process_frame():
    global monitor, current_info
    try:
        if monitor is None:
            return jsonify({"success": False, "error": "System not initialized"}), 500
        
        data = request.json
        image_data = data['image']
        
        # Decode base64 image
        img_str = image_data.split(',')[1]
        img_bytes = base64.b64decode(img_str)
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if img is None:
            return jsonify({"success": False, "error": "Invalid image"}), 400
        
        # Process the frame
        detections = monitor['analyzer'].detect_people(img)
        grid = monitor['mapper'].create_grid(img.shape)
        grid = monitor['mapper'].map_detections_to_grid(grid, detections)
        density_grid, stats = monitor['mapper'].calculate_density(grid)
        
        monitor['predictor'].update_history(stats)
        prediction = monitor['predictor'].predict_future_density(time_minutes=5.0)
        situation, confidence = monitor['classifier'].classify(stats['max_density'], "stable", prediction)
        actions = monitor['classifier'].get_recommended_actions(situation)
        
        monitor['executor'].execute_actions(actions, situation, max_density=stats['max_density'], people_count=stats['total_people'])
        
        with status_lock:
            current_info = {
                "state": situation,
                "confidence": confidence,
                "peopleCount": stats['total_people'],
                "maxDensity": stats['max_density'],
                "trend": "stable",
                "prediction": prediction,
                "actions": actions
            }
            
            # Update logs
            log_time = time.strftime('%H:%M:%S')
            with log_lock:
                live_logs.append({
                    "time": log_time,
                    "zone": situation,
                    "people": stats['total_people'],
                    "density": round(stats['max_density'], 2)
                })
                if len(live_logs) > max_logs:
                    live_logs.pop(0)
        
        return jsonify({
            "success": True,
            "state": situation,
            "confidence": confidence,
            "peopleCount": stats['total_people'],
            "maxDensity": stats['max_density']
        })
        
    except Exception as e:
        print(f"Processing error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/status')
def status():
    global current_info, live_logs, team_alerts, system_running, mode
    with status_lock:
        return jsonify({
            "running": system_running,
            "mode": mode,
            "info": current_info,
            "logs": live_logs,
            "team_alerts": team_alerts
        })

import os

if __name__ == '__main__':
    initialize_system()
    port = int(os.environ.get("PORT", 5000))
    print("\n" + "=" * 80)
    print("SENTINEL AI - Professional Crowd Monitoring System")
    print("=" * 80)
    print(f"Local URL: http://localhost:{port}")
    print("=" * 80 + "\n")
    
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
