/**
 * JARVIS HUD - 3D Neural Mesh Visualization
 * Real-time system monitoring dashboard using Three.js
 * 
 * Displays:
 * - Request latency heatmap
 * - Active agents status
 * - Node health indicators
 * - Network traffic flow
 */

import { useRef, useMemo, useEffect, useState } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { OrbitControls, Text, Line } from '@react-three/drei';
import * as THREE from 'three';

// WebSocket connection for real-time metrics
const WS_URL = import.meta.env.VITE_WS_METRICS_URL || 'ws://localhost:3000/ws/metrics';

/**
 * Neural Node component - represents a system node in the mesh
 */
function NeuralNode({ position, color, size, label, pulse }) {
  const meshRef = useRef();
  const [hovered, setHovered] = useState(false);

  useFrame((state) => {
    if (meshRef.current && pulse) {
      // Pulsing animation for active nodes
      const scale = 1 + Math.sin(state.clock.elapsedTime * 3) * 0.2;
      meshRef.current.scale.set(scale, scale, scale);
    }
  });

  return (
    <group position={position}>
      <mesh
        ref={meshRef}
        onPointerOver={() => setHovered(true)}
        onPointerOut={() => setHovered(false)}
      >
        <sphereGeometry args={[size, 32, 32]} />
        <meshStandardMaterial
          color={hovered ? '#ffffff' : color}
          emissive={color}
          emissiveIntensity={0.5}
          transparent
          opacity={0.8}
        />
      </mesh>
      {hovered && label && (
        <Text
          position={[0, size + 0.3, 0]}
          fontSize={0.15}
          color="white"
          anchorX="center"
          anchorY="middle"
        >
          {label}
        </Text>
      )}
    </group>
  );
}

/**
 * Connection line between nodes - represents data flow
 */
function NodeConnection({ start, end, active, traffic }) {
  const points = useMemo(() => [start, end], [start, end]);
  
  return (
    <Line
      points={points}
      color={active ? '#00ff88' : '#444444'}
      lineWidth={active ? 2 : 1}
      transparent
      opacity={active ? 0.8 : 0.3}
      dashed={!active}
    />
  );
}

/**
 * Metrics Panel - displays real-time statistics
 */
function MetricsPanel({ metrics }) {
  if (!metrics) return null;

  return (
    <div className="absolute top-4 right-4 bg-black/70 backdrop-blur-sm p-4 rounded-lg border border-cyan-500/30 text-cyan-400 font-mono text-xs">
      <h3 className="text-cyan-300 font-bold mb-2 text-sm">SYSTEM METRICS</h3>
      
      <div className="grid grid-cols-2 gap-2">
        <div>
          <div className="text-gray-400">CPU Usage</div>
          <div className="text-white">{(metrics.system?.cpu_usage * 100).toFixed(1)}%</div>
        </div>
        <div>
          <div className="text-gray-400">Memory</div>
          <div className="text-white">{(metrics.system?.memory_usage * 100).toFixed(1)}%</div>
        </div>
        <div>
          <div className="text-gray-400">Connections</div>
          <div className="text-white">{metrics.system?.active_connections}</div>
        </div>
        <div>
          <div className="text-gray-400">Req/sec</div>
          <div className="text-white">{metrics.system?.requests_per_second}</div>
        </div>
        <div>
          <div className="text-gray-400">Avg Latency</div>
          <div className="text-white">{metrics.system?.avg_latency_ms.toFixed(1)}ms</div>
        </div>
        <div>
          <div className="text-gray-400">Status</div>
          <div className={metrics.health?.healthy ? 'text-green-400' : 'text-red-400'}>
            {metrics.health?.healthy ? 'HEALTHY' : 'DEGRADED'}
          </div>
        </div>
      </div>

      <div className="mt-3 pt-3 border-t border-cyan-500/30">
        <div className="text-gray-400 mb-1">AGENTS</div>
        <div className="flex gap-2 text-xs">
          <span className="text-blue-400">Idle: {metrics.agents?.idle}</span>
          <span className="text-green-400">Running: {metrics.agents?.running}</span>
          <span className="text-yellow-400">Paused: {metrics.agents?.paused}</span>
          <span className="text-red-400">Error: {metrics.agents?.error}</span>
        </div>
      </div>
    </div>
  );
}

/**
 * Main Neural Mesh scene
 */
function NeuralMeshScene({ metrics }) {
  // Generate node positions in a spherical distribution
  const nodes = useMemo(() => {
    const nodeCount = 20;
    const positions = [];
    
    for (let i = 0; i < nodeCount; i++) {
      const phi = Math.acos(-1 + (2 * i) / nodeCount);
      const theta = Math.sqrt(nodeCount * Math.PI) * phi;
      
      const radius = 5 + Math.random() * 2;
      const x = radius * Math.cos(theta) * Math.sin(phi);
      const y = radius * Math.sin(theta) * Math.sin(phi);
      const z = radius * Math.cos(phi);
      
      positions.push({
        position: [x, y, z],
        id: `node-${i}`,
        type: i < 5 ? 'core' : i < 15 ? 'agent' : 'edge',
      });
    }
    
    return positions;
  }, []);

  // Determine node colors based on metrics
  const getNodeColor = (type) => {
    if (!metrics?.health?.healthy) return '#ff4444';
    
    switch (type) {
      case 'core': return '#00ffff';
      case 'agent': return '#00ff88';
      case 'edge': return '#8888ff';
      default: return '#444444';
    }
  };

  return (
    <>
      <ambientLight intensity={0.2} />
      <pointLight position={[10, 10, 10]} intensity={1} />
      
      {/* Render nodes */}
      {nodes.map((node) => (
        <NeuralNode
          key={node.id}
          position={node.position}
          color={getNodeColor(node.type)}
          size={node.type === 'core' ? 0.4 : 0.25}
          label={node.id}
          pulse={metrics?.health?.healthy}
        />
      ))}
      
      {/* Render connections between nodes */}
      {nodes.map((node1, i) =>
        nodes.slice(i + 1).map((node2, j) => {
          const distance = Math.sqrt(
            Math.pow(node1.position[0] - node2.position[0], 2) +
            Math.pow(node1.position[1] - node2.position[1], 2) +
            Math.pow(node1.position[2] - node2.position[2], 2)
          );
          
          if (distance < 3) {
            return (
              <NodeConnection
                key={`${node1.id}-${node2.id}`}
                start={node1.position}
                end={node2.position}
                active={metrics?.health?.healthy}
                traffic={metrics?.system?.requests_per_second}
              />
            );
          }
          return null;
        })
      )}
      
      {/* Rotating outer ring */}
      <mesh rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[7, 0.02, 16, 100]} />
        <meshBasicMaterial color="#00ffff" transparent opacity={0.3} />
      </mesh>
    </>
  );
}

/**
 * JARVIS Dashboard Component
 */
export default function JarvisDashboard() {
  const [metrics, setMetrics] = useState(null);
  const wsRef = useRef(null);

  useEffect(() => {
    // Connect to WebSocket for real-time metrics
    wsRef.current = new WebSocket(WS_URL);

    wsRef.current.onopen = () => {
      console.log('JARVIS HUD connected to metrics stream');
    };

    wsRef.current.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setMetrics(data);
      } catch (error) {
        console.error('Failed to parse metrics:', error);
      }
    };

    wsRef.current.onerror = (error) => {
      console.error('WebSocket error:', error);
    };

    wsRef.current.onclose = () => {
      console.log('JARVIS HUD disconnected');
      // Attempt reconnection after 5 seconds
      setTimeout(() => {
        wsRef.current = new WebSocket(WS_URL);
      }, 5000);
    };

    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, []);

  return (
    <div className="w-full h-screen bg-gradient-to-b from-gray-900 via-blue-900 to-gray-900 relative overflow-hidden">
      {/* Header */}
      <div className="absolute top-0 left-0 right-0 z-10 p-4 bg-gradient-to-b from-black/80 to-transparent">
        <h1 className="text-3xl font-bold text-cyan-400 tracking-wider">
          <span className="text-white">J.A.R.V.I.S.</span>
          <span className="text-xs block text-cyan-600 mt-1">
            Just A Rather Very Intelligent System
          </span>
        </h1>
        <p className="text-cyan-300/70 text-sm mt-2">Neural Mesh Monitoring Dashboard</p>
      </div>

      {/* 3D Canvas */}
      <Canvas camera={{ position: [0, 0, 15], fov: 60 }}>
        <NeuralMeshScene metrics={metrics} />
        <OrbitControls
          enableZoom={true}
          enablePan={false}
          autoRotate={true}
          autoRotateSpeed={0.5}
        />
      </Canvas>

      {/* Metrics Panel */}
      <MetricsPanel metrics={metrics} />

      {/* Footer info */}
      <div className="absolute bottom-4 left-4 text-cyan-600 text-xs font-mono">
        <div>WebSocket: {wsRef.current?.readyState === WebSocket.OPEN ? 'CONNECTED' : 'DISCONNECTED'}</div>
        <div>Last Update: {metrics?.timestamp ? new Date(metrics.timestamp).toLocaleTimeString() : 'N/A'}</div>
      </div>
    </div>
  );
}
