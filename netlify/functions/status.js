// Netlify Serverless Function: status.js
// Handles /status and /api/status requests dynamically with simulated live transit telemetry

let frameId = 1200;
let baseCount = 28;

exports.handler = async function(event, context) {
  frameId += 1;
  const nowUtc = new Date().toISOString();
  
  // Simulate live fluctuation
  const jitter = Math.floor(Math.random() * 7) - 3;
  const peopleCount = Math.max(8, Math.min(85, baseCount + jitter));
  const occupancyIndex = parseFloat((peopleCount / 120).toFixed(3));
  
  const responseData = {
    operating_mode: "SIMULATION",
    simulation_loop_count: 1,
    simulation_source_label: "Express Corridor Station Live Feed",
    simulation_source_name: "crowd_station.mp4",
    default_simulation_available: true,
    default_simulation_metadata: {
      duration_s: 18.4,
      fps: 30.0,
      frame_count: 552,
      height: 720,
      width: 1280
    },
    runtime_health: {
      camera_health: "LIVE",
      state: "HEALTHY",
      snapshot_fresh: true,
      worker_alive: true,
      consecutive_failures: 0,
      snapshot_age_ms: 32.4,
      last_success_at: nowUtc
    },
    connectivity: {
      state: "ONLINE",
      remote_endpoint_status: "CONNECTED",
      current_outage_duration_s: 0.0,
      total_outage_duration_s: 0.0,
      last_success_at: nowUtc
    },
    snapshot: {
      frame_id: frameId,
      timestamp_utc: nowUtc,
      frame_age_ms: 28.5,
      processing_latency_ms: 18.2,
      source_mode: "SIMULATION",
      camera_health: "LIVE",
      model_version: "yolov8s.pt",
      people_count: peopleCount,
      occupancy_index: occupancyIndex,
      confidence: 0.88,
      severity: occupancyIndex > 0.65 ? "RED" : (occupancyIndex > 0.4 ? "YELLOW" : "GREEN"),
      hotspot: "r1c3",
      primary_scenario: occupancyIndex > 0.65 ? "LOCAL_BOTTLENECK" : (occupancyIndex > 0.4 ? "ACCUMULATION" : "STABLE_HIGH_OCCUPANCY"),
      load_anomaly: occupancyIndex > 0.4 ? 0.35 : 0.08,
      accumulation: occupancyIndex > 0.4 ? 0.28 : 0.04,
      redistribution: 0.12,
      recommended_action: occupancyIndex > 0.65 
        ? "Regulate inflow at Concourse Gate 2; divert flow to North Foot Over Bridge." 
        : "Nominal passenger flow across all platforms. Routine safety monitoring.",
      occupancy_grid: [
        [3, 4, 12, 8, 3, 1],
        [2, 5, 18, 14, 4, 2],
        [1, 2, 4, 3, 1, 0],
        [0, 1, 1, 1, 0, 0]
      ]
    },
    metrics: {
      connectivity_state: "ONLINE",
      events_generated: 148,
      events_synced: 148,
      events_pending: 0,
      events_lost: 0,
      events_local_delivered: 148,
      events_local_acknowledged: 14,
      sync_attempts: 148,
      retry_attempts: 0,
      system_started_at: new Date(Date.now() - 3600000).toISOString()
    },
    local_alerts: [
      {
        event_id: "evt-netlify-" + frameId,
        created_at_utc: nowUtc,
        frame_id: frameId,
        severity: "YELLOW",
        primary_scenario: "ACCUMULATION",
        hotspot: "r1c3",
        action_code: "PREPARE_INFLOW_CONTROL",
        recommended_action: "Prepare alternate routing away from emerging chokepoint.",
        local_status: "LOCAL_DELIVERED",
        sync_status: "SYNCED"
      }
    ],
    recent_events: [
      {
        event_id: "evt-netlify-" + frameId,
        created_at_utc: nowUtc,
        frame_id: frameId,
        severity: "YELLOW",
        primary_scenario: "ACCUMULATION",
        hotspot: "r1c3",
        local_status: "LOCAL_DELIVERED",
        sync_status: "SYNCED"
      }
    ]
  };

  return {
    statusCode: 200,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store, no-cache, must-revalidate",
      "Access-Control-Allow-Origin": "*"
    },
    body: JSON.stringify(responseData)
  };
};
