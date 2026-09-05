// Netlify Serverless Function: camera-settings.js
exports.handler = async function(event, context) {
  return {
    statusCode: 200,
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*"
    },
    body: JSON.stringify({
      backend: "NETLIFY-EDGE-SIMULATION",
      brightness: 50,
      contrast: 50,
      exposure: -1,
      height: 720,
      restart_count: 0,
      source: "0 (Simulation Fallback)",
      target_fps: 30,
      width: 1280
    })
  };
};
