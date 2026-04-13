import React, { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  Activity, 
  Inbox, 
  LayoutDashboard, 
  Zap, 
  Brain, 
  History, 
  Plus, 
  Target,
  Terminal,
  Send,
  ChevronRight,
  Calendar,
  Network
} from 'lucide-react';
import ForceGraph2D from 'react-force-graph-2d';
import { 
  BarChart, 
  Bar, 
  XAxis, 
  YAxis, 
  Tooltip, 
  ResponsiveContainer, 
  Cell,
  PieChart,
  Pie
} from 'recharts';
import axios from 'axios';

type ActionSpaceNode = {
  id: string;
  kind: 'context' | 'policy' | 'action' | 'target';
  column: 'contexts' | 'policies' | 'actions' | 'targets';
  label: string;
  subtitle?: string;
  active?: boolean;
  recenter_path?: string;
  group?: string;
  friction_score?: number;
};

type ActionSpaceEdge = {
  from: string;
  to: string;
  kind: 'active' | 'available' | 'requires_user' | 'default';
  reason: string;
};

type ActionSpacePayload = {
  center: {
    path: string;
    level: string;
    agent: string;
    mode: string;
    domain?: string | null;
    project?: string | null;
  };
  nodes: ActionSpaceNode[];
  edges: ActionSpaceEdge[];
};

const ACTION_SPACE_COLUMNS: Array<ActionSpaceNode['column']> = ['contexts', 'policies', 'actions', 'targets'];
const ACTION_SPACE_LABELS: Record<ActionSpaceNode['column'], string> = {
  contexts: 'Contexts',
  policies: 'Policies',
  actions: 'Moves',
  targets: 'Destinations',
};

const SidebarIcon = ({ icon: Icon, active, onClick, label }: any) => (
  <motion.div
    whileHover={{ scale: 1.1 }}
    whileTap={{ scale: 0.9 }}
    onClick={onClick}
    title={label}
    aria-label={label}
    className={`p-3 rounded-xl cursor-pointer transition-all duration-300 ${
      active ? 'bg-neon-cyan/20 text-neon-cyan shadow-[0_0_15px_rgba(102,252,241,0.3)]' : 'text-gray-500 hover:text-white'
    }`}
  >
    <Icon size={24} />
  </motion.div>
);

const GlassPanel = ({ children, className }: any) => (
  <div className={`backdrop-blur-xl bg-white/5 border border-white/10 rounded-2xl p-6 ${className}`}>
    {children}
  </div>
);

const nodeClasses = (node: ActionSpaceNode) => {
  const base = 'w-full rounded-2xl border px-4 py-3 text-left transition-all duration-200';
  if (node.kind === 'context') {
    if ((node.friction_score || 0) > 0.6) {
      return `${base} border-cyber-magenta bg-cyber-magenta/10 shadow-[0_0_20px_rgba(255,0,127,0.2)] animate-pulse`;
    }
    return `${base} ${node.active ? 'border-neon-cyan bg-neon-cyan/10 shadow-[0_0_20px_rgba(102,252,241,0.12)]' : 'border-white/10 bg-white/5 hover:border-neon-cyan/30'}`;
  }
  if (node.kind === 'policy') {
    return `${base} border-cyber-magenta/30 bg-cyber-magenta/5`;
  }
  if (node.kind === 'action') {
    return `${base} border-white/15 bg-white/[0.03]`;
  }
  if (node.group === 'promotion') {
    return `${base} border-muted-amber/30 bg-muted-amber/5`;
  }
  return `${base} border-white/10 bg-white/5`;
};

const edgeColor = (kind: ActionSpaceEdge['kind']) => {
  if (kind === 'active') return 'rgba(102,252,241,0.8)';
  if (kind === 'requires_user') return 'rgba(242,169,0,0.8)';
  return 'rgba(255,255,255,0.18)';
};

const ActionSpaceGraph = ({
  graph,
  onRecenter,
}: {
  graph: ActionSpacePayload;
  onRecenter: (path: string) => void;
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const nodeRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const [paths, setPaths] = useState<Array<ActionSpaceEdge & { d: string }>>([]);

  useEffect(() => {
    const updatePaths = () => {
      const container = containerRef.current;
      if (!container) return;
      const containerRect = container.getBoundingClientRect();
      const nextPaths = graph.edges.flatMap((edge) => {
        const source = nodeRefs.current[edge.from];
        const target = nodeRefs.current[edge.to];
        if (!source || !target) return [];
        const sourceRect = source.getBoundingClientRect();
        const targetRect = target.getBoundingClientRect();
        const x1 = sourceRect.right - containerRect.left;
        const y1 = sourceRect.top - containerRect.top + sourceRect.height / 2;
        const x2 = targetRect.left - containerRect.left;
        const y2 = targetRect.top - containerRect.top + targetRect.height / 2;
        const bend = Math.max((x2 - x1) / 2, 32);
        return [{ ...edge, d: `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}` }];
      });
      setPaths(nextPaths);
    };

    const frame = window.requestAnimationFrame(updatePaths);
    window.addEventListener('resize', updatePaths);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('resize', updatePaths);
    };
  }, [graph]);

  return (
    <div ref={containerRef} className="relative min-h-[560px] overflow-auto rounded-2xl border border-white/10 bg-[#0f1117]/70">
      <svg className="pointer-events-none absolute inset-0 h-full w-full">
        <defs>
          <marker id="actionSpaceArrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" fill="rgba(255,255,255,0.5)" />
          </marker>
        </defs>
        {paths.map((edge) => (
          <path
            key={`${edge.from}-${edge.to}`}
            d={edge.d}
            fill="none"
            stroke={edgeColor(edge.kind)}
            strokeWidth={edge.kind === 'active' ? 2.4 : 1.4}
            strokeDasharray={edge.kind === 'requires_user' ? '5 5' : undefined}
            markerEnd="url(#actionSpaceArrow)"
          />
        ))}
      </svg>

      <div className="relative z-10 grid min-w-[980px] grid-cols-4 gap-4 p-4">
        {ACTION_SPACE_COLUMNS.map((column) => (
          <div key={column} className="flex flex-col gap-3">
            <div className="px-1 text-[10px] font-mono uppercase tracking-[0.3em] text-gray-500">
              {ACTION_SPACE_LABELS[column]}
            </div>
            {graph.nodes
              .filter((node) => node.column === column)
              .map((node) => {
                const clickable = Boolean(node.recenter_path);
                const content = (
                  <div
                    ref={(element) => {
                      nodeRefs.current[node.id] = element;
                    }}
                    className={nodeClasses(node)}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="text-sm font-medium text-white">{node.label}</div>
                        {node.subtitle && (
                          <div className="mt-1 break-all font-mono text-[10px] uppercase tracking-[0.18em] text-gray-500">
                            {node.subtitle}
                          </div>
                        )}
                      </div>
                      {node.active && (
                        <span className="rounded-full border border-neon-cyan/30 bg-neon-cyan/10 px-2 py-1 font-mono text-[9px] uppercase tracking-[0.2em] text-neon-cyan">
                          Active
                        </span>
                      )}
                    </div>
                  </div>
                );

                if (!clickable) {
                  return <div key={node.id}>{content}</div>;
                }
                return (
                  <button key={node.id} type="button" className="text-left" onClick={() => onRecenter(node.recenter_path!)}>
                    {content}
                  </button>
                );
              })}
          </div>
        ))}
      </div>
    </div>
  );
};

function App() {
  const [activeTab, setActiveTab] = useState('radar');
  const [projects, setProjects] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [offloadInput, setOffloadInput] = useState('');
  const [showConfirmation, setShowConfirmation] = useState(false);
  const [candidates, setCandidates] = useState<any[]>([]);
  const [health, setHealth] = useState({ energy: 'medium', readiness: 'medium', stress: 'low' });
  const [telemetry, setTelemetry] = useState<any>(null);
  const [agents, setAgents] = useState<any[]>([]);
  const [timeline, setTimeline] = useState<any[]>([]);
  const [calendar, setCalendar] = useState<any[]>([]);
  const [loops, setLoops] = useState<any[]>([]);
  const [graphData, setGraphData] = useState<any>(null);
  const [isomorphs, setIsomorphs] = useState<any[]>([]);
  
  // ADHD UX States
  const [isLowStim, setIsLowStim] = useState(false);
  const [showLaunchpad, setShowLaunchpad] = useState(true);
  
  // Cockpit / Correction State
  const [cockpitProject, setCockpitProject] = useState<any>(null);
  const [isEditingLine, setIsEditingLine] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");

  const fetchCockpit = async (path: string) => {
    try {
      const response = await axios.get(`http://localhost:8000/api/project-cockpit?project_path=${path}`);
      setCockpitProject(response.data);
    } catch (error) {
      console.error("Failed to fetch cockpit", error);
    }
  };

  const handleSurgicalEdit = async (file: string, original: string, newVal: string | null, action: 'edit' | 'delete') => {
    try {
      await axios.post('http://localhost:8000/api/edit-contract', {
        file_path: `${cockpitProject.path}/${file}`,
        original_line: original,
        new_line: newVal,
        action: action
      });
      // Refresh local state
      fetchCockpit(cockpitProject.path);
      setIsEditingLine(null);
    } catch (error) {
      console.error("Surgical edit failed", error);
    }
  };

  useEffect(() => {
    // Auto-enable Low-Stim if energy is low
    if (health.energy === 'low' || health.stress === 'high') {
      setIsLowStim(true);
    }
  }, [health]);

  useEffect(() => {
    const fetchGraph = async () => {
      try {
        const [graphRes, isomorphRes] = await Promise.all([
          axios.get('http://localhost:8000/api/graph'),
          axios.get('http://localhost:8000/api/isomorph')
        ]);
        setGraphData(graphRes.data);
        setIsomorphs(isomorphRes.data.isomorphs);
      } catch (error) {
        console.error("Failed to fetch graph/isomorphs", error);
      }
    };
    if (activeTab === 'graph') fetchGraph();
  }, [activeTab]);
  const [isFocusMode, setIsFocusMode] = useState(false);
  const [focusTimer, setFocusTimer] = useState(0);

  useEffect(() => {
    let interval: any;
    if (isFocusMode) {
      interval = setInterval(() => setFocusTimer(prev => prev + 1), 1000);
    } else {
      setFocusTimer(0);
    }
    return () => clearInterval(interval);
  }, [isFocusMode]);

  const handleActivate = async (projectPath: string) => {
    try {
      await axios.post(`http://localhost:8000/api/activate?project_path=${projectPath}`);
      setIsFocusMode(true);
      setActiveTab('radar');
    } catch (error) {
      console.error("Failed to activate project", error);
    }
  };

  const handleRouteLoop = async (loop: any, target: string) => {
    try {
      await axios.post(`http://localhost:8000/api/loops/route?target_project=${target}`, loop);
      setLoops(prev => prev.filter(l => l.id !== loop.id));
    } catch (error) {
      console.error("Failed to route loop", error);
    }
  };

  const [actionSpace, setActionSpace] = useState<ActionSpacePayload | null>(null);
  const [actionSpacePath, setActionSpacePath] = useState('.');
  const [actionSpaceLoading, setActionSpaceLoading] = useState(false);
  const [actionSpaceError, setActionSpaceError] = useState('');

  useEffect(() => {
    const fetchCalendar = async () => {
      try {
        const response = await axios.get('http://localhost:8000/api/calendar');
        setCalendar(response.data.events);
      } catch (error) {
        console.error("Failed to fetch calendar data", error);
      }
    };
    if (activeTab === 'calendar') fetchCalendar();
  }, [activeTab]);

  // Console / Harness state

  const [consoleVisible, setConsoleVisible] = useState(false);
  const [consoleCommand, setConsoleCommand] = useState('');
  const [consoleOutput, setConsoleOutput] = useState<any[]>([]);
  const [isExecuting, setIsExecuting] = useState(false);
  const consoleEndRef = useRef<null | HTMLDivElement>(null);

  const fetchData = async () => {
    try {
      setLoading(true);
      const [radarRes, triageRes, teleRes, agentsRes, journalRes, loopsRes] = await Promise.all([
        axios.get('http://localhost:8000/api/radar'),
        axios.get('http://localhost:8000/api/triage'),
        axios.get('http://localhost:8000/api/telemetry'),
        axios.get('http://localhost:8000/api/agents'),
        axios.get('http://localhost:8000/api/journal'),
        axios.get('http://localhost:8000/api/loops')
      ]);
      setProjects(radarRes.data.projects);
      setCandidates(triageRes.data.candidates);
      setTelemetry(teleRes.data);
      setAgents(agentsRes.data.agents);
      setTimeline(journalRes.data.events);
      setLoops(loopsRes.data.loops);
    } catch (error) {
      console.error("Failed to fetch data", error);
    } finally {
      setLoading(false);
    }
  };

  const fetchActionSpace = async (path: string) => {
    try {
      setActionSpaceLoading(true);
      setActionSpaceError('');
      const response = await axios.get('http://localhost:8000/api/action-space', {
        params: { path },
      });
      setActionSpace(response.data);
    } catch (error) {
      console.error("Failed to fetch action space", error);
      setActionSpaceError("Action-space graph unavailable");
    } finally {
      setActionSpaceLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, [activeTab]);

  useEffect(() => {
    if (activeTab === 'radar') {
      fetchActionSpace(actionSpacePath);
    }
  }, [activeTab, actionSpacePath]);

  useEffect(() => {
    consoleEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [consoleOutput]);

  const handleOffload = async (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && offloadInput.trim()) {
      const content = offloadInput;
      try {
        setOffloadInput(''); // Immediate clear for "Vacuum" effect
        setShowConfirmation(true);
        await axios.post('http://localhost:8000/api/offload', { content });
        setTimeout(() => setShowConfirmation(false), 2000);
      } catch (error) {
        console.error("Failed to offload thought", error);
        setOffloadInput(content); // Revert if failed
      }
    }
  };

  const handlePromote = async (candidate: any) => {
    try {
      await axios.post('http://localhost:8000/api/triage/promote', candidate);
      setCandidates(prev => prev.filter(c => c.id !== candidate.id));
    } catch (error) {
      console.error("Failed to promote signal", error);
    }
  };

  const handleReject = async (candidate: any) => {
    try {
      await axios.post('http://localhost:8000/api/triage/reject', candidate);
      setCandidates(prev => prev.filter(c => c.id !== candidate.id));
    } catch (error) {
      console.error("Failed to reject signal", error);
    }
  };

  const updateHealth = async (newHealth: any) => {
    setHealth(newHealth);
    try {
      await axios.post('http://localhost:8000/api/health/update', {
        energy_now: newHealth.energy,
        cognitive_readiness_now: newHealth.readiness,
        stress_load_now: newHealth.stress
      });
    } catch (error) {
      console.error("Failed to update health", error);
    }
  };

  const executeCommand = async () => {
    if (!consoleCommand.trim()) return;
    setIsExecuting(true);
    const cmd = consoleCommand;
    setConsoleCommand('');
    setConsoleOutput(prev => [...prev, { type: 'input', content: cmd }]);
    
    try {
      const response = await axios.post('http://localhost:8000/api/execute', {
        agent: 'chief-of-staff',
        mode: 'conversation',
        context: '.',
        prompt: cmd
      });
      
      if (response.data.error) {
        setConsoleOutput(prev => [...prev, { type: 'error', content: response.data.error }]);
      } else {
        setConsoleOutput(prev => [...prev, { type: 'output', content: response.data.stdout || response.data.stderr }]);
      }
    } catch (error) {
      setConsoleOutput(prev => [...prev, { type: 'error', content: "Network error: Backend unreachable" }]);
    } finally {
      setIsExecuting(false);
    }
  };

  const COLORS = ['#66fcf1', '#ff007f', '#f2a900', '#4e54c8', '#8f94fb'];

  return (
    <div className="flex h-screen w-screen bg-[#0b0c10] text-white overflow-hidden font-sans p-4 gap-4">
      {/* Sidebar */}
      <nav className="w-20 flex flex-col items-center py-8 gap-8 backdrop-blur-xl bg-white/5 border border-white/10 rounded-3xl">
        <div className="text-neon-cyan mb-8">
          <Brain size={32} />
        </div>
        <SidebarIcon icon={LayoutDashboard} active={activeTab === 'radar'} onClick={() => setActiveTab('radar')} label="Radar" />
        <SidebarIcon icon={Inbox} active={activeTab === 'inbox'} onClick={() => setActiveTab('inbox')} label="Signal Triage" />
        <SidebarIcon icon={Plus} active={activeTab === 'loops'} onClick={() => setActiveTab('loops')} label="Anxiety Triage" />
        <SidebarIcon icon={Brain} active={activeTab === 'forge'} onClick={() => setActiveTab('forge')} label="Forge" />
        <SidebarIcon icon={Activity} active={activeTab === 'telemetry'} onClick={() => setActiveTab('telemetry')} label="Telemetry" />
        <SidebarIcon icon={Zap} active={activeTab === 'health'} onClick={() => setActiveTab('health')} label="Health" />
        <SidebarIcon icon={Network} active={activeTab === 'graph'} onClick={() => setActiveTab('graph')} label="Neural Graph" />
        <SidebarIcon icon={Calendar} active={activeTab === 'calendar'} onClick={() => setActiveTab('calendar')} label="Calendar" />
        <SidebarIcon icon={History} active={activeTab === 'journal'} onClick={() => setActiveTab('journal')} label="Journal" />
        <div className="mt-auto flex flex-col gap-4">
          <SidebarIcon 
            icon={isLowStim ? Zap : Target} 
            onClick={() => setIsLowStim(!isLowStim)} 
            label={isLowStim ? "High Stim Mode" : "Low Stim Mode"} 
            active={isLowStim}
          />
          <SidebarIcon icon={Terminal} onClick={() => setConsoleVisible(!consoleVisible)} label="Terminal" active={consoleVisible} />
        </div>
      </nav>

      {/* Main Content */}
      <main className={`flex-1 flex flex-col gap-4 overflow-hidden relative transition-all duration-700 ${isLowStim ? 'grayscale-[0.5] contrast-[0.8]' : ''}`}>
        
        {/* Launchpad Hero Overlay */}
        <AnimatePresence>
          {showLaunchpad && activeTab === 'radar' && (
            <motion.div 
              initial={{ opacity: 0, scale: 1.1 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, y: -100 }}
              className="absolute inset-0 z-40 bg-[#0b0c10] flex flex-col items-center justify-center p-12"
            >
              <div className="max-w-3xl w-full space-y-12">
                 <div className="space-y-4">
                    <span className="text-neon-cyan font-mono text-xs uppercase tracking-[0.6em]">Recommended Path //</span>
                    <h1 className="text-7xl font-black tracking-tighter uppercase leading-[0.9]">
                      {projects.find(p => p.status === 'active')?.name || "Ready to Start"}
                    </h1>
                    <p className="text-2xl text-gray-500 font-medium italic">
                      "{projects.find(p => p.status === 'active')?.current_focus || "Select a new context to begin."}"
                    </p>
                 </div>

                 <div className="flex gap-4">
                    <button 
                      onClick={() => {
                        const activeProj = projects.find(p => p.status === 'active');
                        if (activeProj) handleActivate(activeProj.path);
                        setShowLaunchpad(false);
                      }}
                      className="px-12 py-6 bg-neon-cyan text-black text-xl font-black uppercase tracking-widest rounded-2xl shadow-[0_0_40px_rgba(102,252,241,0.4)] hover:scale-105 transition-all"
                    >
                      Start Session
                    </button>
                    <button 
                      onClick={() => setShowLaunchpad(false)}
                      className="px-8 py-6 bg-white/5 border border-white/10 text-gray-400 text-sm font-bold uppercase tracking-widest rounded-2xl hover:bg-white/10 transition-all"
                    >
                      View All Projects
                    </button>
                 </div>
                 
                 <div className="pt-12 border-t border-white/5 flex gap-12">
                    <div>
                       <div className="text-[10px] font-mono text-gray-600 uppercase mb-2">Cognitive Health</div>
                       <div className={`text-sm font-bold uppercase ${health.energy === 'low' ? 'text-red-400' : 'text-neon-cyan'}`}>
                          {health.energy} Energy // {health.stress} Stress
                       </div>
                    </div>
                    <div>
                       <div className="text-[10px] font-mono text-gray-600 uppercase mb-2">Open Loops</div>
                       <div className="text-sm font-bold text-cyber-magenta uppercase">{loops.length} Processing</div>
                    </div>
                 </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
        {/* Project Cockpit Overlay */}
        <AnimatePresence>
          {cockpitProject && (
            <motion.div 
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="absolute inset-0 z-50 bg-[#0b0c10]/95 backdrop-blur-3xl flex flex-col p-8"
            >
              <div className="flex justify-between items-center mb-8">
                 <div className="flex items-center gap-4">
                    <button 
                      onClick={() => setCockpitProject(null)}
                      className="p-2 hover:bg-white/10 rounded-full transition-colors"
                    >
                      <ChevronRight className="rotate-180" size={24} />
                    </button>
                    <div>
                      <h2 className="text-4xl font-black text-white uppercase tracking-tighter">{cockpitProject.name}</h2>
                      <p className="text-xs font-mono text-neon-cyan/50 tracking-widest">{cockpitProject.path}</p>
                    </div>
                 </div>
                 <div className="flex gap-4">
                    <button 
                      onClick={() => handleActivate(cockpitProject.path)}
                      className="px-6 py-2 bg-neon-cyan text-black rounded-xl font-black text-xs uppercase tracking-widest shadow-[0_0_20px_#66fcf1]"
                    >
                      Enter Focus Mode
                    </button>
                 </div>
              </div>

              <div className="grid grid-cols-3 gap-6 flex-1 overflow-hidden pb-8">
                 {[
                   { title: 'Current State', key: 'state', file: 'STATE.md', icon: Activity, color: 'text-neon-cyan' },
                   { title: 'Durable Memory', key: 'memory', file: 'MEMORY.md', icon: Brain, color: 'text-cyber-magenta' },
                   { title: 'Decision Rules', key: 'rules', file: 'DECISION RULES.md', icon: Zap, color: 'text-muted-amber' }
                 ].map((section) => (
                   <GlassPanel key={section.key} className="flex flex-col h-full overflow-hidden border-white/5">
                      <div className="flex items-center gap-3 mb-6">
                         <section.icon size={18} className={section.color} />
                         <h3 className="font-mono text-sm uppercase tracking-widest font-bold">{section.title}</h3>
                      </div>
                      
                      <div className="flex-1 overflow-y-auto space-y-3 custom-scrollbar pr-2">
                         {cockpitProject[section.key].length === 0 ? (
                           <div className="text-[10px] font-mono text-gray-600 italic">Contract surface is empty.</div>
                         ) : cockpitProject[section.key].map((line: string, idx: number) => (
                           <div key={idx} className="group relative p-3 bg-white/[0.02] border border-white/5 rounded-xl hover:border-white/20 transition-all">
                              {isEditingLine === `${section.key}-${idx}` ? (
                                <div className="flex flex-col gap-2">
                                  <textarea 
                                    className="w-full bg-black/40 border border-neon-cyan/50 rounded-lg p-2 text-xs font-mono focus:outline-none"
                                    value={editValue}
                                    onChange={(e) => setEditValue(e.target.value)}
                                    rows={3}
                                  />
                                  <div className="flex gap-2">
                                     <button 
                                      onClick={() => handleSurgicalEdit(section.file, line, editValue, 'edit')}
                                      className="px-3 py-1 bg-neon-cyan text-black rounded-lg text-[10px] font-bold uppercase"
                                     >
                                       Save
                                     </button>
                                     <button 
                                      onClick={() => setIsEditingLine(null)}
                                      className="px-3 py-1 bg-white/10 rounded-lg text-[10px] font-bold uppercase"
                                     >
                                       Cancel
                                     </button>
                                  </div>
                                </div>
                              ) : (
                                <>
                                  <p className="text-xs text-gray-300 leading-relaxed font-medium">
                                    {line.replace("- ", "")}
                                  </p>
                                  <div className="absolute top-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                                     <button 
                                      onClick={() => {
                                        setIsEditingLine(`${section.key}-${idx}`);
                                        setEditValue(line);
                                      }}
                                      className="p-1.5 hover:bg-neon-cyan/20 hover:text-neon-cyan rounded-lg text-gray-500 transition-colors"
                                     >
                                       <Plus size={14} className="rotate-45" /> {/* Edit icon proxy */}
                                     </button>
                                     <button 
                                      onClick={() => handleSurgicalEdit(section.file, line, null, 'delete')}
                                      className="p-1.5 hover:bg-red-500/20 hover:text-red-400 rounded-lg text-gray-500 transition-colors"
                                     >
                                       <Plus size={14} className="rotate-45" /> {/* Delete icon proxy */}
                                     </button>
                                  </div>
                                </>
                              )}
                           </div>
                         ))}
                      </div>
                      
                      <button className="mt-4 w-full py-2 bg-white/5 border border-dashed border-white/10 rounded-xl text-[10px] font-mono text-gray-500 hover:text-white hover:border-white/30 transition-all uppercase tracking-widest">
                        + Append Signal
                      </button>
                   </GlassPanel>
                 ))}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
        <header className="h-16 flex items-center justify-between px-4 shrink-0">
          <div className="flex items-center gap-2">
            <span className="text-xs font-mono text-gray-500 uppercase tracking-widest">System Status //</span>
            <span className="text-xs font-mono text-neon-cyan uppercase animate-pulse">Nominal</span>
          </div>
          
          <div className="relative group w-1/2">
            <input 
              type="text" 
              placeholder="Press Enter to offload thought..."
              className={`w-full bg-white/5 border rounded-full py-2 px-6 focus:outline-none transition-all duration-300 text-sm italic ${
                showConfirmation ? 'border-neon-cyan bg-neon-cyan/10' : 'border-white/10 focus:border-cyber-magenta/50 focus:bg-white/10'
              }`}
              value={showConfirmation ? "THOUGHT OFFLOADED // NOMINAL" : offloadInput}
              onChange={(e) => setOffloadInput(e.target.value)}
              onKeyDown={handleOffload}
              disabled={showConfirmation}
            />
            <div className={`absolute right-4 top-1/2 -translate-y-1/2 transition-colors ${
              showConfirmation ? 'text-neon-cyan' : 'text-cyber-magenta/50 group-hover:text-cyber-magenta'
            }`}>
              {showConfirmation ? <Zap size={18} className="animate-pulse" /> : <Plus size={18} />}
            </div>
          </div>

          <div className="flex items-center gap-4">
            <div className="flex flex-col items-end">
              <span className="text-[10px] text-gray-500 uppercase tracking-tighter">Readiness</span>
              <div className="w-24 h-1.5 bg-white/10 rounded-full overflow-hidden">
                <div className="h-full bg-neon-cyan w-3/4 shadow-[0_0_10px_#66fcf1]" />
              </div>
            </div>
          </div>
        </header>

        {/* Dynamic Content Container */}
        <div className="flex-1 min-h-0 overflow-hidden relative">
          {activeTab === 'radar' && (
            <div className="h-full overflow-y-auto pr-2 custom-scrollbar">
              <div className="grid grid-cols-12 gap-4">
                <GlassPanel className="col-span-8 flex flex-col gap-4 h-[600px]">
                  <div className="flex items-center justify-between">
                    <h2 className="text-lg font-mono flex items-center gap-2 uppercase tracking-widest">
                      <Target className="text-neon-cyan" size={20} />
                      Action Space
                    </h2>
                    <span className="text-[10px] text-gray-500 font-mono tracking-widest">DETERMINISTIC POLICY GRAPH</span>
                  </div>
                  
                  <div className="flex-1 min-h-0 overflow-hidden border border-white/5 rounded-xl bg-black/20">
                    {actionSpaceLoading && (
                      <div className="flex h-full items-center justify-center font-mono text-xs uppercase tracking-[0.3em] text-gray-500">
                        Rendering graph...
                      </div>
                    )}
                    {!actionSpaceLoading && actionSpaceError && (
                      <div className="flex h-full items-center justify-center font-mono text-xs uppercase tracking-[0.2em] text-red-300">
                        {actionSpaceError}
                      </div>
                    )}
                    {!actionSpaceLoading && actionSpace && (
                      <ActionSpaceGraph graph={actionSpace} onRecenter={setActionSpacePath} />
                    )}
                  </div>
                </GlassPanel>

                <div className="col-span-4 flex flex-col gap-4">
                  <GlassPanel className="flex-1 flex flex-col gap-4 border-cyber-magenta/20 min-h-[300px]">
                    <h2 className="text-sm font-mono flex items-center gap-2 text-cyber-magenta">
                      <Zap size={16} />
                      SYSTEM COCKPIT
                    </h2>
                    <div className="space-y-3">
                      {loading ? (
                        <div className="animate-pulse h-4 bg-white/5 rounded" />
                      ) : actionSpace ? (
                        <>
                          <div className="rounded-lg border border-white/10 bg-white/5 p-3 flex justify-between items-center">
                            <div>
                              <div className="text-[10px] font-mono uppercase tracking-[0.25em] text-gray-500">Active Context</div>
                              <div className="mt-1 break-all text-sm text-white font-bold uppercase tracking-widest">{actionSpace.center.path}</div>
                            </div>
                            <button 
                              onClick={() => handleActivate(actionSpace.center.path)}
                              className="px-4 py-2 bg-neon-cyan text-black rounded-xl font-black text-[10px] uppercase tracking-[0.2em] shadow-[0_0_15px_rgba(102,252,241,0.5)] hover:scale-105 transition-all"
                            >
                              Activate
                            </button>
                          </div>

                          <div className="grid grid-cols-2 gap-3">
                            <div className="rounded-lg border border-white/10 bg-white/5 p-3">
                              <div className="text-[10px] font-mono uppercase tracking-[0.25em] text-gray-500">Agent</div>
                              <div className="mt-2 text-sm text-neon-cyan font-bold">{actionSpace.center.agent}</div>
                            </div>
                            <div className="rounded-lg border border-white/10 bg-white/5 p-3">
                              <div className="text-[10px] font-mono uppercase tracking-[0.25em] text-gray-500">Mode</div>
                              <div className="mt-2 text-sm text-cyber-magenta font-bold">{actionSpace.center.mode}</div>
                            </div>
                          </div>
                          <div className="rounded-lg border border-white/10 bg-white/5 p-3">
                            <div className="text-[10px] font-mono uppercase tracking-[0.25em] text-gray-500">Friction Level</div>
                            <div className="mt-2 flex items-center gap-3">
                              <div className="flex-1 h-1.5 bg-white/10 rounded-full overflow-hidden">
                                <div className="h-full bg-cyber-magenta" style={{ width: '45%' }} />
                              </div>
                              <span className="text-xs font-mono text-white font-bold">45%</span>
                            </div>
                          </div>
                        </>
                      ) : (
                        <div className="animate-pulse h-4 bg-white/5 rounded" />
                      )}
                    </div>
                  </GlassPanel>
                  
                  <GlassPanel className="h-56 flex flex-col gap-4 bg-neon-cyan/5 border-neon-cyan/10">
                     <div className="text-xs font-mono text-gray-500 uppercase tracking-widest">Session Guidelines</div>
                     <div className="space-y-2 text-[10px] text-gray-400 font-mono italic leading-relaxed">
                       <p>&gt; EXECUTION MODE ACTIVE</p>
                       <p>&gt; BRANCHING REDUCED BY 40%</p>
                       <p>&gt; PREFER NARROW NEXT ACTIONS</p>
                       <p>&gt; OFFLOAD ALL SIDE-TRACKS</p>
                     </div>
                  </GlassPanel>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'inbox' && (
            <div className="h-full overflow-y-auto pr-2 custom-scrollbar">
              <div className="flex flex-col items-center justify-center min-h-[500px] gap-8 py-12">
                <div className="text-center">
                  <h2 className="text-2xl font-mono text-neon-cyan tracking-[0.2em] uppercase font-black">Signal Triage</h2>
                  <p className="text-xs text-gray-500 font-mono tracking-widest mt-2 uppercase">Reviewing inferred intent into tracked open loops</p>
                </div>

                <div className="relative w-full max-w-md h-80">
                  <AnimatePresence mode="wait">
                    {candidates.slice(0, 1).map((candidate) => (
                      <motion.div
                        key={candidate.id}
                        initial={{ scale: 0.9, opacity: 0, y: 20 }}
                        animate={{ scale: 1, opacity: 1, y: 0 }}
                        exit={{ x: 500, opacity: 0, rotate: 20, transition: { duration: 0.3 } }}
                        className="absolute inset-0"
                      >
                        <GlassPanel className="h-full flex flex-col justify-between border-neon-cyan/30 shadow-[0_0_40px_rgba(102,252,241,0.1)]">
                          <div className="space-y-4">
                            <div className="flex justify-between items-start">
                              <span className="text-[10px] font-mono px-3 py-1 rounded bg-neon-cyan/20 text-neon-cyan uppercase tracking-widest font-bold">
                                {candidate.queue_section || candidate.type}
                              </span>
                              <span className="text-[10px] font-mono text-gray-600 truncate max-w-[150px]">
                                {candidate.source_file}
                              </span>
                            </div>
                            <p className="text-base leading-relaxed italic text-gray-100 font-medium">
                              "{candidate.content}"
                            </p>
                          </div>
                          
                          <div className="flex gap-4 pt-6">
                            <button 
                              onClick={() => handlePromote(candidate)}
                              className="flex-1 py-4 bg-neon-cyan text-black rounded-xl font-black text-xs uppercase tracking-widest shadow-[0_0_20px_rgba(102,252,241,0.3)] hover:scale-[1.02] transition-all"
                            >
                              Confirm
                            </button>
                            <button 
                              onClick={() => setCandidates(prev => prev.filter(c => c.id !== candidate.id))}
                              className="flex-1 py-4 bg-white/5 border border-white/10 rounded-xl font-bold text-xs uppercase tracking-widest text-gray-500 hover:text-white hover:bg-white/10 transition-all"
                            >
                              Reject
                            </button>
                          </div>
                        </GlassPanel>
                      </motion.div>
                    ))}
                  </AnimatePresence>
                  
                  {candidates.length === 0 && (
                    <div className="flex flex-col items-center justify-center h-full text-gray-600 font-mono italic">
                      <Inbox size={64} className="mb-6 opacity-5" />
                      SIGNAL QUEUE NOMINAL // ALL DATA DURABLE
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {activeTab === 'loops' && (
             <div className="h-full overflow-y-auto pr-2 custom-scrollbar">
                <div className="flex flex-col gap-6 py-4">
                  <div className="text-center">
                    <h2 className="text-2xl font-mono text-cyber-magenta tracking-widest uppercase font-black">Anxiety Triage</h2>
                    <p className="text-[10px] text-gray-500 font-mono tracking-widest mt-1 uppercase">Processing raw signal and clearing mental overhead</p>
                  </div>

                  <div className="grid grid-cols-12 gap-6">
                     <div className="col-span-7 flex flex-col gap-4">
                        {loops.length === 0 ? (
                          <div className="flex flex-col items-center justify-center py-24 text-gray-600 font-mono italic opacity-40">
                             <Zap size={64} className="mb-6" />
                             COGNITIVE LOAD NOMINAL // ALL LOOPS CLOSED
                          </div>
                        ) : loops.map((loop: any) => (
                          <motion.div 
                            key={loop.id}
                            initial={{ opacity: 0, x: -20 }}
                            animate={{ opacity: 1, x: 0 }}
                          >
                            <GlassPanel className="flex flex-col gap-6 border-cyber-magenta/10 hover:border-cyber-magenta/30 transition-all">
                               <p className="text-base font-medium italic text-gray-200">"{loop.content}"</p>
                               <div className="flex gap-3">
                                  <select 
                                    onChange={(e) => handleRouteLoop(loop, e.target.value)}
                                    className="flex-1 bg-white/5 border border-white/10 rounded-xl px-4 py-3 text-xs font-mono text-gray-300 focus:outline-none focus:border-neon-cyan/50"
                                  >
                                    <option value="">Route to Destination...</option>
                                    {projects.map((p: any) => (
                                      <option key={p.path} value={p.path}>{p.name.toUpperCase()}</option>
                                    ))}
                                  </select>
                                  <button 
                                    onClick={() => setLoops(prev => prev.filter(l => l.id !== loop.id))}
                                    className="px-6 py-3 bg-white/5 hover:bg-red-500/20 text-gray-500 hover:text-red-400 border border-white/10 rounded-xl text-[10px] font-black tracking-widest transition-all uppercase"
                                  >
                                    Clear
                                  </button>
                               </div>
                            </GlassPanel>
                          </motion.div>
                        ))}
                     </div>

                     <div className="col-span-5">
                        <GlassPanel className="sticky top-0 bg-cyber-magenta/5 border-cyber-magenta/20 flex flex-col justify-center items-center text-center py-12">
                           <div className="p-8 rounded-full bg-cyber-magenta/10 mb-8 shadow-[0_0_40px_rgba(255,0,127,0.3)]">
                              <Activity size={48} className="text-cyber-magenta" />
                           </div>
                           <h3 className="text-5xl font-black text-white uppercase tracking-tighter">{loops.length}</h3>
                           <p className="text-xs font-mono text-gray-500 uppercase tracking-[0.4em] mt-3 font-bold">Active Open Loops</p>
                           <div className="w-24 h-[1px] bg-white/10 my-10" />
                           <p className="text-sm text-gray-400 italic max-w-[250px] leading-relaxed">"Your brain is for having ideas, not holding them."</p>
                        </GlassPanel>
                     </div>
                  </div>
                </div>
             </div>
          )}

          {activeTab === 'telemetry' && telemetry && (
             <div className="h-full overflow-y-auto pr-2 custom-scrollbar">
               <div className="grid grid-cols-12 gap-4 py-4 pb-24">
                 <GlassPanel className="col-span-12 h-36 flex items-center justify-between bg-white/5 border-neon-cyan/10">
                    <div className="flex flex-col">
                      <span className="text-[10px] font-mono text-gray-500 uppercase tracking-[0.4em] font-bold">Operational Throughput</span>
                      <span className="text-6xl font-black text-neon-cyan tracking-tighter">{telemetry.total_sessions} <span className="text-xl font-normal text-gray-600">INTEL SESSIONS</span></span>
                    </div>
                    <div className="flex gap-20">
                       <div className="text-right">
                          <div className="text-[10px] font-mono text-gray-500 uppercase tracking-widest mb-2">Primary Mode</div>
                          <div className="text-3xl font-black uppercase text-white tracking-tighter">{Object.keys(telemetry.modes)[0] || 'N/A'}</div>
                       </div>
                       <div className="text-right">
                          <div className="text-[10px] font-mono text-gray-500 uppercase tracking-widest mb-2">Lead Agent</div>
                          <div className="text-3xl font-black uppercase text-white tracking-tighter">{Object.keys(telemetry.agents)[0] || 'N/A'}</div>
                       </div>
                    </div>
                 </GlassPanel>

                 <GlassPanel className="col-span-12 h-[350px] flex flex-col bg-neon-cyan/[0.02] border-neon-cyan/20">
                    <h3 className="text-xs font-mono text-neon-cyan uppercase tracking-widest mb-8 flex items-center gap-2 font-bold">
                      <Brain size={16} />
                      Identity Auditor // Empirical Reality Check
                    </h3>
                    <div className="flex-1 flex gap-12">
                       <div className="flex-1 min-h-0 pb-4">
                          <ResponsiveContainer width="100%" height="100%">
                             <BarChart data={Object.entries(telemetry.hourly_distribution || {}).map(([h, d]) => ({ hour: `${h}:00`, density: d }))}>
                                <Bar dataKey="density" fill="#66fcf1" radius={[3, 3, 0, 0]} />
                             </BarChart>
                          </ResponsiveContainer>
                       </div>
                       <div className="w-80 flex flex-col justify-center gap-6 border-l border-white/5 pl-12">
                          <div>
                             <span className="text-[10px] font-mono text-gray-500 uppercase tracking-widest block mb-2">Optimal Focus Window</span>
                             <div className="text-4xl font-black text-neon-cyan tracking-tighter">{telemetry.peak_hour}</div>
                          </div>
                          <p className="text-xs text-gray-400 italic leading-relaxed">
                            Maximum decision density occurs in this window. Redirect high-complexity planning to this time slot for 30% improved efficiency.
                          </p>
                       </div>
                    </div>
                 </GlassPanel>

                 <GlassPanel className="col-span-7 h-[450px] flex flex-col">
                    <h3 className="text-xs font-mono text-gray-500 uppercase tracking-widest mb-10 flex items-center gap-2 font-bold">
                      <Activity size={16} className="text-neon-cyan" />
                      Operational Mode Matrix
                    </h3>
                    <div className="flex-1 min-h-0">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={Object.entries(telemetry.modes).map(([m, c]) => ({ mode: m.toUpperCase(), count: c }))}>
                          <XAxis dataKey="mode" axisLine={false} tickLine={false} tick={{ fill: '#4b5563', fontSize: 10, fontFamily: 'monospace' }} />
                          <YAxis hide />
                          <Tooltip 
                            cursor={{ fill: 'rgba(102, 252, 241, 0.03)' }}
                            contentStyle={{ backgroundColor: '#0b0c10', border: '1px solid rgba(102, 252, 241, 0.2)', borderRadius: '12px' }}
                          />
                          <Bar dataKey="count" radius={[6, 6, 0, 0]}>
                            {Object.entries(telemetry.modes).map((_, index) => (
                              <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                            ))}
                          </Bar>
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                 </GlassPanel>

                 <GlassPanel className="col-span-5 h-[450px] flex flex-col">
                    <h3 className="text-xs font-mono text-gray-500 uppercase tracking-widest mb-10 flex items-center gap-2 font-bold">
                      <Brain size={16} className="text-cyber-magenta" />
                      Intelligence Allocation
                    </h3>
                    <div className="flex-1 min-h-0 flex items-center justify-center">
                      <ResponsiveContainer width="100%" height="100%">
                        <PieChart>
                          <Pie
                            data={Object.entries(telemetry.agents).map(([a, c]) => ({ name: a, value: c }))}
                            innerRadius={80}
                            outerRadius={110}
                            paddingAngle={8}
                            dataKey="value"
                            stroke="none"
                          >
                            {Object.entries(telemetry.agents).map((_, index) => (
                              <Cell key={`cell-${index}`} fill={COLORS[(index + 1) % COLORS.length]} />
                            ))}
                          </Pie>
                          <Tooltip 
                            contentStyle={{ backgroundColor: '#0b0c10', border: '1px solid rgba(102, 252, 241, 0.2)', borderRadius: '12px' }}
                          />
                        </PieChart>
                      </ResponsiveContainer>
                      <div className="absolute flex flex-col items-center">
                         <span className="text-[8px] font-mono text-gray-500 uppercase">System</span>
                         <span className="text-xl font-black text-white">ROSTER</span>
                      </div>
                    </div>
                 </GlassPanel>
               </div>
             </div>
          )}

          {activeTab === 'forge' && (
            <div className="h-full overflow-y-auto pr-2 custom-scrollbar">
               <div className="flex flex-col gap-8 py-4">
                 <div className="flex items-center justify-between">
                    <div>
                      <h2 className="text-3xl font-black text-neon-cyan tracking-widest uppercase italic">Agent Forge</h2>
                      <p className="text-[10px] text-gray-500 font-mono tracking-widest mt-1 uppercase font-bold">Directing the static intelligence roster</p>
                    </div>
                    <button className="px-8 py-3 bg-neon-cyan text-black rounded-full text-[10px] font-black hover:shadow-[0_0_20px_#66fcf1] transition-all uppercase tracking-widest">
                      + Initialize New Entity
                    </button>
                 </div>

                 <div className="grid grid-cols-3 gap-6 pb-24">
                    {agents.map((agent: any) => (
                      <motion.div key={agent.name} whileHover={{ y: -8 }} transition={{ duration: 0.3 }}>
                        <GlassPanel className="h-80 flex flex-col justify-between hover:border-neon-cyan/40 transition-all duration-500 group relative overflow-hidden">
                          <div className="absolute top-0 right-0 w-32 h-32 bg-neon-cyan/5 blur-3xl rounded-full -mr-16 -mt-16 group-hover:bg-neon-cyan/10 transition-all" />
                          <div className="relative z-10">
                            <div className="flex justify-between items-start">
                               <div className="p-4 bg-neon-cyan/10 rounded-2xl text-neon-cyan group-hover:shadow-[0_0_25px_rgba(102,252,241,0.4)] transition-all">
                                 <Brain size={28} />
                               </div>
                               <span className="text-[10px] font-mono text-gray-600 uppercase tracking-widest font-bold">Status: Ready</span>
                            </div>
                            <h3 className="text-2xl font-black mt-8 uppercase tracking-tighter text-white group-hover:text-neon-cyan transition-colors">{agent.name}</h3>
                            <p className="text-xs text-gray-400 mt-4 line-clamp-4 italic leading-relaxed">
                              {agent.description}
                            </p>
                          </div>
                          
                          <div className="flex items-center justify-between pt-8 border-t border-white/5 relative z-10">
                             <div className="flex items-center gap-3">
                               <div className="w-2.5 h-2.5 rounded-full bg-neon-cyan shadow-[0_0_10px_#66fcf1] animate-pulse" />
                               <span className="text-[10px] font-mono text-neon-cyan uppercase tracking-[0.2em] font-black">Nominal</span>
                             </div>
                             <button className="text-[10px] font-mono text-gray-500 hover:text-white uppercase tracking-widest border border-white/10 px-4 py-2 rounded-full hover:bg-white/5 transition-all">Configure</button>
                          </div>
                        </GlassPanel>
                      </motion.div>
                    ))}
                 </div>
               </div>
            </div>
          )}

          {activeTab === 'health' && (
            <div className="h-full overflow-y-auto pr-2 custom-scrollbar">
              <div className="grid grid-cols-12 gap-6 py-4 pb-24">
                <div className="col-span-12 text-center mb-8">
                  <h2 className="text-5xl font-black text-neon-cyan tracking-tighter uppercase italic">Scaffolding Control</h2>
                  <p className="text-[10px] text-gray-500 font-mono tracking-[0.6em] uppercase mt-4 font-bold">Synchronizing system adaptation to human readiness metrics</p>
                </div>

                {[
                  { label: 'Energy', key: 'energy', icon: Zap, options: ['low', 'medium', 'high'], color: 'text-neon-cyan' },
                  { label: 'Readiness', key: 'readiness', icon: Brain, options: ['low', 'medium', 'high'], color: 'text-cyber-magenta' },
                  { label: 'Stress', key: 'stress', icon: Target, options: ['low', 'medium', 'high'], color: 'text-muted-amber' },
                ].map((metric) => (
                  <GlassPanel key={metric.key} className="col-span-4 flex flex-col items-center gap-10 py-16 border-white/5 hover:bg-white/[0.02] transition-all duration-500">
                    <div className={`p-6 rounded-[2.5rem] bg-white/5 ${metric.color}/20`}>
                      <metric.icon size={48} className={metric.color} />
                    </div>
                    <h3 className="font-mono text-base tracking-[0.5em] uppercase font-black">{metric.label}</h3>
                    <div className="flex gap-4">
                      {metric.options.map((opt) => (
                        <button
                          key={opt}
                          onClick={() => updateHealth({ ...health, [metric.key]: opt })}
                          className={`px-8 py-4 rounded-xl text-[10px] font-black uppercase tracking-widest transition-all duration-500 border-2 ${
                            (health as any)[metric.key] === opt 
                            ? `bg-white text-[#0b0c10] border-white shadow-[0_0_30px_rgba(255,255,255,0.4)]` 
                            : 'bg-transparent text-gray-600 border-white/5 hover:border-white/20 hover:text-white'
                          }`}
                        >
                          {opt}
                        </button>
                      ))}
                    </div>
                  </GlassPanel>
                ))}

                <GlassPanel className="col-span-12 bg-neon-cyan/5 border-neon-cyan/20 min-h-48 relative overflow-hidden group">
                  <div className="absolute top-0 right-0 p-6 opacity-5 group-hover:opacity-10 transition-opacity">
                    <Terminal size={150} />
                  </div>
                  <div className="relative z-10">
                    <div className="flex items-center gap-4 mb-8 text-neon-cyan">
                      <Activity size={24} className="animate-pulse" />
                      <span className="text-base font-mono uppercase tracking-[0.4em] font-black">System Response Console</span>
                    </div>
                    <div className="font-mono text-xs space-y-4 opacity-90">
                      <div className="flex gap-6">
                         <span className="text-gray-600">[{new Date().toLocaleTimeString()}]</span>
                         <span className="text-neon-cyan font-bold">{`> ENERGY_LEVEL_DETECTED: ${health.energy.toUpperCase()}`}</span>
                      </div>
                      <div className="flex gap-6">
                         <span className="text-gray-600">[{new Date().toLocaleTimeString()}]</span>
                         <span className="text-gray-300">{`> INJECTING ADAPTATION PROTOCOLS...`}</span>
                      </div>
                      <div className="flex gap-6">
                         <span className="text-gray-600">[{new Date().toLocaleTimeString()}]</span>
                         <span className="text-black bg-neon-cyan px-3 py-1 rounded font-black">
                          {health.energy === 'low' 
                            ? 'ACTIVATE: LOW_ENERGY_SCAFFOLDING // DIRECTIVE_TONE=TRUE // BRANCHING=FALSE' 
                            : 'ACTIVATE: NOMINAL_SCAFFOLDING // ALL_COGNITIVE_PATHS=OPEN'}
                         </span>
                      </div>
                    </div>
                  </div>
                </GlassPanel>
              </div>
            </div>
          )}

          {activeTab === 'inbox' && (
            <div className="flex flex-col items-center justify-center min-h-[500px] gap-8">
              <div className="text-center">
                <h2 className="text-2xl font-mono text-neon-cyan tracking-[0.2em] uppercase">Signal Triage</h2>
                <p className="text-xs text-gray-500 font-mono tracking-widest mt-2 uppercase">Reviewing inferred intent into tracked open loops</p>
              </div>

              <div className="relative w-[400px] h-72">
                <AnimatePresence mode="wait">
                  {candidates.slice(0, 1).map((candidate) => (
                    <motion.div
                      key={candidate.id}
                      initial={{ scale: 0.9, opacity: 0, y: 20 }}
                      animate={{ scale: 1, opacity: 1, y: 0 }}
                      exit={{ x: 500, opacity: 0, rotate: 20, transition: { duration: 0.3 } }}
                      className="absolute inset-0"
                    >
                      <GlassPanel className="h-full flex flex-col justify-between border-neon-cyan/30 shadow-[0_0_30px_rgba(102,252,241,0.1)] group">
                        <div>
                          <div className="flex justify-between items-start">
                            <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-neon-cyan/20 text-neon-cyan uppercase tracking-tighter">
                              {candidate.queue_section || candidate.type}
                            </span>
                            <span className="text-[10px] font-mono text-gray-500">
                              {candidate.source_file}
                            </span>
                          </div>
                          <p className="mt-6 text-sm leading-relaxed italic text-gray-200 font-medium">
                            "{candidate.content}"
                          </p>
                        </div>
                        
                        <div className="flex gap-2 mt-auto pt-6">
                          <button 
                            onClick={() => handlePromote(candidate)}
                            className="flex-1 py-3 bg-neon-cyan/10 hover:bg-neon-cyan text-neon-cyan hover:text-black border border-neon-cyan/30 rounded-xl text-[10px] font-mono tracking-widest transition-all duration-300 uppercase font-bold"
                          >
                            Confirm Open Loop
                          </button>
                          <button 
                            onClick={() => handleReject(candidate)}
                            className="flex-1 py-3 bg-white/5 hover:bg-white/10 border border-white/10 rounded-xl text-[10px] font-mono tracking-widest transition-all duration-300 uppercase text-gray-500 hover:text-white"
                          >
                            Reject Signal
                          </button>
                        </div>
                      </GlassPanel>
                    </motion.div>
                  ))}
                </AnimatePresence>
                
                {candidates.length === 0 && (
                  <div className="flex flex-col items-center justify-center h-full text-gray-500 font-mono italic">
                    <Inbox size={48} className="mb-4 opacity-10" />
                    INTENT QUEUE NOMINAL // NO PENDING INFERRED OPEN LOOPS
                  </div>
                )}
              </div>
            </div>
          )}

          {activeTab === 'loops' && (
             <div className="flex flex-col gap-6 h-full overflow-hidden">
                <div className="text-center mb-4">
                  <h2 className="text-2xl font-mono text-cyber-magenta tracking-widest uppercase">Anxiety Triage</h2>
                  <p className="text-[10px] text-gray-500 font-mono tracking-widest mt-1 uppercase">Processing raw signal and clearing mental overhead</p>
                </div>

                <div className="grid grid-cols-12 gap-6 flex-1 overflow-hidden pb-8">
                   {/* Loops List */}
                   <div className="col-span-7 flex flex-col gap-4 overflow-y-auto pr-4 custom-scrollbar">
                      {loops.length === 0 ? (
                        <div className="flex flex-col items-center justify-center h-full text-gray-600 font-mono italic opacity-40">
                           <Zap size={48} className="mb-4" />
                           COGNITIVE LOAD NOMINAL // ALL LOOPS CLOSED
                        </div>
                      ) : loops.map((loop: any) => (
                        <motion.div 
                          key={loop.id}
                          initial={{ opacity: 0, x: -20 }}
                          animate={{ opacity: 1, x: 0 }}
                        >
                          <GlassPanel className="flex flex-col gap-4 border-cyber-magenta/10 hover:border-cyber-magenta/30 transition-all group">
                             <p className="text-sm font-medium italic text-gray-200">"{loop.content}"</p>
                             <div className="flex gap-2">
                                <select 
                                  onChange={(e) => handleRouteLoop(loop, e.target.value)}
                                  className="flex-1 bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-[10px] font-mono text-gray-400 focus:outline-none focus:border-neon-cyan/50"
                                >
                                  <option value="">Route to Destination...</option>
                                  {projects.map((p: any) => (
                                    <option key={p.path} value={p.path}>{p.name.toUpperCase()}</option>
                                  ))}
                                </select>
                                <button 
                                  onClick={() => setLoops(prev => prev.filter(l => l.id !== loop.id))}
                                  className="px-4 py-2 bg-white/5 hover:bg-red-500/20 text-gray-500 hover:text-red-400 border border-white/10 rounded-lg text-[10px] font-mono transition-all uppercase"
                                >
                                  Delete
                                </button>
                             </div>
                          </GlassPanel>
                        </motion.div>
                      ))}
                   </div>

                   {/* Stats Sidebar */}
                   <div className="col-span-5 flex flex-col gap-4">
                      <GlassPanel className="flex-1 bg-cyber-magenta/5 border-cyber-magenta/20 flex flex-col justify-center items-center text-center">
                         <div className="p-6 rounded-full bg-cyber-magenta/10 mb-6 shadow-[0_0_30px_rgba(255,0,127,0.2)]">
                            <Activity size={40} className="text-cyber-magenta" />
                         </div>
                         <h3 className="text-3xl font-black text-white uppercase tracking-tighter">{loops.length}</h3>
                         <p className="text-[10px] font-mono text-gray-500 uppercase tracking-widest mt-2">Active Open Loops</p>
                         <div className="w-full h-[1px] bg-white/5 my-8" />
                         <p className="text-xs text-gray-400 italic max-w-[200px]">"Your brain is for having ideas, not holding them."</p>
                      </GlassPanel>
                   </div>
                </div>
             </div>
          )}

          {activeTab === 'telemetry' && telemetry && (
             <div className="grid grid-cols-12 gap-4 h-full pb-8">
               <GlassPanel className="col-span-12 h-32 flex items-center justify-between bg-white/5 border-neon-cyan/10">
                  <div className="flex flex-col">
                    <span className="text-[10px] font-mono text-gray-500 uppercase tracking-[0.3em]">Operational Throughput</span>
                    <span className="text-5xl font-black text-neon-cyan tracking-tighter">{telemetry.total_sessions} <span className="text-xl font-normal text-gray-500">SESSIONS</span></span>
                  </div>
                  <div className="flex gap-16">
                     <div className="text-right">
                        <div className="text-[10px] font-mono text-gray-500 uppercase tracking-widest mb-1">Primary Mode</div>
                        <div className="text-2xl font-bold uppercase text-white tracking-tighter">{Object.keys(telemetry.modes)[0] || 'N/A'}</div>
                     </div>
                     <div className="text-right">
                        <div className="text-[10px] font-mono text-gray-500 uppercase tracking-widest mb-1">Lead Agent</div>
                        <div className="text-2xl font-bold uppercase text-white tracking-tighter">{Object.keys(telemetry.agents)[0] || 'N/A'}</div>
                     </div>
                  </div>
               </GlassPanel>

               <GlassPanel className="col-span-12 h-64 flex flex-col bg-neon-cyan/[0.02] border-neon-cyan/20">
                  <h3 className="text-xs font-mono text-neon-cyan uppercase tracking-widest mb-6 flex items-center gap-2">
                    <Brain size={14} />
                    Identity Auditor // Empirical Reality Check
                  </h3>
                  <div className="flex-1 flex gap-8">
                     <div className="flex-1 min-h-0">
                        <ResponsiveContainer width="100%" height="100%">
                           <BarChart data={Object.entries(telemetry.hourly_distribution || {}).map(([h, d]) => ({ hour: `${h}:00`, density: d }))}>
                              <Bar dataKey="density" fill="#66fcf1" radius={[2, 2, 0, 0]} />
                           </BarChart>
                        </ResponsiveContainer>
                     </div>
                     <div className="w-64 flex flex-col justify-center gap-4 border-l border-white/10 pl-8 font-mono text-[10px]">
                        <div>
                           <span className="text-gray-500 uppercase">Peak Cognitive Window</span>
                           <div className="text-lg text-neon-cyan font-bold">{telemetry.peak_hour}</div>
                        </div>
                        <p className="text-gray-400 italic">
                          Empirical data suggests your current Self-Model may be outdated. 
                          Maximum decision density occurs in the morning hours.
                        </p>
                     </div>
                  </div>
               </GlassPanel>

               <GlassPanel className="col-span-7 h-[400px] flex flex-col">
                  <h3 className="text-xs font-mono text-gray-500 uppercase tracking-widest mb-8 flex items-center gap-2">
                    <Activity size={14} className="text-neon-cyan" />
                    Mode Distribution Matrix
                  </h3>
                  <div className="flex-1 min-h-0">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={Object.entries(telemetry.modes).map(([m, c]) => ({ mode: m.toUpperCase(), count: c }))}>
                        <XAxis dataKey="mode" axisLine={false} tickLine={false} tick={{ fill: '#4b5563', fontSize: 10, fontFamily: 'monospace' }} />
                        <YAxis hide />
                        <Tooltip 
                          cursor={{ fill: 'rgba(102, 252, 241, 0.05)' }}
                          contentStyle={{ backgroundColor: '#0b0c10', border: '1px solid rgba(102, 252, 241, 0.2)', borderRadius: '8px' }}
                        />
                        <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                          {Object.entries(telemetry.modes).map((_, index) => (
                            <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                          ))}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
               </GlassPanel>

               <GlassPanel className="col-span-5 h-[400px] flex flex-col">
                  <h3 className="text-xs font-mono text-gray-500 uppercase tracking-widest mb-8 flex items-center gap-2">
                    <Brain size={14} className="text-cyber-magenta" />
                    Agent Resource Allocation
                  </h3>
                  <div className="flex-1 min-h-0">
                    <ResponsiveContainer width="100%" height="100%">
                      <PieChart>
                        <Pie
                          data={Object.entries(telemetry.agents).map(([a, c]) => ({ name: a, value: c }))}
                          innerRadius={60}
                          outerRadius={80}
                          paddingAngle={5}
                          dataKey="value"
                        >
                          {Object.entries(telemetry.agents).map((_, index) => (
                            <Cell key={`cell-${index}`} fill={COLORS[(index + 1) % COLORS.length]} />
                          ))}
                        </Pie>
                        <Tooltip 
                          contentStyle={{ backgroundColor: '#0b0c10', border: '1px solid rgba(102, 252, 241, 0.2)', borderRadius: '8px' }}
                        />
                      </PieChart>
                    </ResponsiveContainer>
                  </div>
               </GlassPanel>
             </div>
          )}

          {activeTab === 'forge' && (
            <div className="flex flex-col gap-6 h-full overflow-hidden">
               <div className="flex items-center justify-between px-4">
                  <div>
                    <h2 className="text-2xl font-mono text-neon-cyan tracking-widest uppercase">Agent Forge</h2>
                    <p className="text-[10px] text-gray-500 font-mono tracking-widest mt-1 uppercase">Directing the static intelligence roster</p>
                  </div>
                  <button className="px-6 py-2 bg-neon-cyan/10 border border-neon-cyan/20 rounded-full text-[10px] font-mono text-neon-cyan hover:bg-neon-cyan/20 transition-all uppercase tracking-widest">
                    Initialize New Entity
                  </button>
               </div>

               <div className="grid grid-cols-3 gap-6 overflow-y-auto pb-12 pr-4 custom-scrollbar">
                  {agents.map((agent: any) => (
                    <motion.div key={agent.name} whileHover={{ y: -8 }} transition={{ duration: 0.3 }}>
                      <GlassPanel className="h-72 flex flex-col justify-between hover:border-neon-cyan/40 transition-all duration-500 group relative overflow-hidden">
                        <div className="absolute top-0 right-0 w-32 h-32 bg-neon-cyan/5 blur-3xl rounded-full -mr-16 -mt-16 group-hover:bg-neon-cyan/10 transition-all" />
                        <div className="relative z-10">
                          <div className="flex justify-between items-start">
                             <div className="p-3 bg-neon-cyan/10 rounded-2xl text-neon-cyan group-hover:shadow-[0_0_20px_rgba(102,252,241,0.4)] transition-all">
                               <Brain size={24} />
                             </div>
                             <span className="text-[10px] font-mono text-gray-600 uppercase tracking-widest">Static // L0</span>
                          </div>
                          <h3 className="text-2xl font-black mt-6 uppercase tracking-tighter text-white group-hover:text-neon-cyan transition-colors">{agent.name}</h3>
                          <p className="text-xs text-gray-400 mt-3 line-clamp-3 italic leading-relaxed">
                            {agent.description}
                          </p>
                        </div>
                        
                        <div className="flex items-center justify-between pt-6 border-t border-white/5 relative z-10">
                           <div className="flex items-center gap-3">
                             <div className="w-2 h-2 rounded-full bg-neon-cyan shadow-[0_0_8px_#66fcf1] animate-pulse" />
                             <span className="text-[10px] font-mono text-neon-cyan uppercase tracking-[0.2em] font-bold">Nominal</span>
                           </div>
                           <button className="text-[10px] font-mono text-gray-500 hover:text-white uppercase tracking-widest border border-white/10 px-3 py-1 rounded-full hover:bg-white/5 transition-all">Configure</button>
                        </div>
                      </GlassPanel>
                    </motion.div>
                  ))}
               </div>
            </div>
          )}

          {activeTab === 'health' && (
            <div className="grid grid-cols-12 gap-6 p-4 h-full">
              <div className="col-span-12 text-center mb-6">
                <h2 className="text-4xl font-black text-neon-cyan tracking-tighter uppercase italic">Scaffolding Control</h2>
                <p className="text-[10px] text-gray-500 font-mono tracking-[0.5em] uppercase mt-3">Synchronizing system adaptation to human readiness metrics</p>
              </div>

              {[
                { label: 'Energy', key: 'energy', icon: Zap, options: ['low', 'medium', 'high'], color: 'text-neon-cyan' },
                { label: 'Readiness', key: 'readiness', icon: Brain, options: ['low', 'medium', 'high'], color: 'text-cyber-magenta' },
                { label: 'Stress', key: 'stress', icon: Target, options: ['low', 'medium', 'high'], color: 'text-muted-amber' },
              ].map((metric) => (
                <GlassPanel key={metric.key} className="col-span-4 flex flex-col items-center gap-8 py-12 border-white/5 hover:border-white/10 transition-all duration-500">
                  <div className={`p-5 rounded-3xl bg-white/5 ${metric.color}/20`}>
                    <metric.icon size={40} className={metric.color} />
                  </div>
                  <h3 className="font-mono text-sm tracking-[0.4em] uppercase font-bold">{metric.label}</h3>
                  <div className="flex gap-3">
                    {metric.options.map((opt) => (
                      <button
                        key={opt}
                        onClick={() => updateHealth({ ...health, [metric.key]: opt })}
                        className={`px-6 py-3 rounded-xl text-[10px] font-mono uppercase tracking-widest transition-all duration-500 border ${
                          (health as any)[metric.key] === opt 
                          ? `bg-white text-[#0b0c10] border-white font-bold shadow-[0_0_20px_rgba(255,255,255,0.3)]` 
                          : 'bg-white/5 text-gray-500 border-white/5 hover:border-white/20 hover:text-white'
                        }`}
                      >
                        {opt}
                      </button>
                    ))}
                  </div>
                </GlassPanel>
              ))}

              <GlassPanel className="col-span-12 bg-neon-cyan/5 border-neon-cyan/20 min-h-40 relative overflow-hidden group">
                <div className="absolute top-0 right-0 p-4 opacity-5 group-hover:opacity-10 transition-opacity">
                  <Terminal size={120} />
                </div>
                <div className="relative z-10">
                  <div className="flex items-center gap-3 mb-6 text-neon-cyan">
                    <Activity size={20} className="animate-pulse" />
                    <span className="text-sm font-mono uppercase tracking-[0.3em] font-bold">System Response Console</span>
                  </div>
                  <div className="font-mono text-xs space-y-3 opacity-90">
                    <div className="flex gap-4">
                       <span className="text-gray-500">[{new Date().toLocaleTimeString()}]</span>
                       <span className="text-neon-cyan">{`> ENERGY_LEVEL_DETECTED: ${health.energy.toUpperCase()}`}</span>
                    </div>
                    <div className="flex gap-4">
                       <span className="text-gray-500">[{new Date().toLocaleTimeString()}]</span>
                       <span>{`> INJECTING ADAPTATION PROTOCOLS...`}</span>
                    </div>
                    <div className="flex gap-4">
                       <span className="text-gray-500">[{new Date().toLocaleTimeString()}]</span>
                       <span className="text-white bg-neon-cyan/20 px-2 py-0.5 rounded">
                        {health.energy === 'low' 
                          ? 'ACTIVATE: LOW_ENERGY_SCAFFOLDING // DIRECTIVE_TONE=TRUE // BRANCHING=FALSE' 
                          : 'ACTIVATE: NOMINAL_SCAFFOLDING // ALL_COGNITIVE_PATHS=OPEN'}
                       </span>
                    </div>
                  </div>
                </div>
              </GlassPanel>
            </div>
          )}

          {activeTab === 'calendar' && (
            <div className="flex flex-col gap-6 h-full overflow-hidden">
                <div className="text-center mb-6">
                  <h2 className="text-3xl font-black text-neon-cyan tracking-tighter uppercase italic">Automation Schedule</h2>
                  <p className="text-[10px] text-gray-500 font-mono tracking-[0.5em] mt-3 uppercase">Chronological registry of recursive system operations</p>
                </div>

                <div className="flex-1 overflow-y-auto space-y-6 pb-12 pr-4 custom-scrollbar">
                   {calendar.length === 0 ? (
                     <div className="flex flex-col items-center justify-center h-full text-gray-500 font-mono italic">
                        <Calendar size={48} className="mb-4 opacity-10" />
                        NO AUTOMATIONS SCHEDULED // SYSTEM MANUALLY OPERATED
                     </div>
                   ) : calendar.map((job: any, idx: number) => (
                     <motion.div 
                        key={idx}
                        initial={{ opacity: 0, scale: 0.95 }}
                        animate={{ opacity: 1, scale: 1 }}
                        transition={{ delay: idx * 0.1 }}
                     >
                       <GlassPanel className="flex flex-col gap-4 border-neon-cyan/10 hover:border-neon-cyan/40 transition-all group">
                          <div className="flex items-center justify-between">
                             <div className="flex items-center gap-4">
                                <div className="p-3 rounded-xl bg-neon-cyan/10 text-neon-cyan group-hover:shadow-[0_0_20px_rgba(102,252,241,0.3)] transition-all">
                                   <Zap size={20} />
                                </div>
                                <div className="flex flex-col">
                                   <h3 className="text-xl font-bold uppercase tracking-tight">{job.name}</h3>
                                   <div className="flex items-center gap-2 mt-1">
                                      <span className="text-[10px] font-mono text-neon-cyan px-2 py-0.5 rounded bg-neon-cyan/10">FREQUENCY: {job.schedule}</span>
                                   </div>
                                </div>
                             </div>
                             <div className="text-right">
                                <span className="text-[10px] font-mono text-gray-500 uppercase tracking-widest">Type // Recurring</span>
                             </div>
                          </div>
                          
                          <div className="grid grid-cols-2 gap-4 py-4 border-t border-white/5">
                             <div>
                                <span className="text-[10px] font-mono text-gray-500 uppercase tracking-widest block mb-2">Purpose</span>
                                <p className="text-xs text-gray-300 italic">{job.purpose}</p>
                             </div>
                             <div>
                                <span className="text-[10px] font-mono text-gray-500 uppercase tracking-widest block mb-2">Command</span>
                                <div className="bg-black/40 p-3 rounded-lg border border-white/5 group-hover:border-neon-cyan/20 transition-colors">
                                   <code className="text-[10px] font-mono text-neon-cyan/80 break-all">{job.command}</code>
                                </div>
                             </div>
                          </div>
                       </GlassPanel>
                     </motion.div>
                   ))}
                </div>
            </div>
          )}

          {activeTab === 'graph' && (
             <div className="flex flex-col gap-6 h-full overflow-hidden">
                <div className="text-center">
                  <h2 className="text-2xl font-black text-neon-cyan tracking-widest uppercase">Neural Graph</h2>
                  <p className="text-[10px] text-gray-500 font-mono tracking-widest mt-1 uppercase">Visualizing inter-domain cognitive associations</p>
                </div>
                
                <GlassPanel className="flex-1 bg-black/40 border-white/5 p-0 overflow-hidden relative">
                   {graphData ? (
                     <ForceGraph2D
                       graphData={graphData}
                       nodeLabel="id"
                       nodeColor={(node: any) => node.group === 'system' ? '#ff007f' : '#66fcf1'}
                       nodeRelSize={6}
                       linkColor={() => 'rgba(255, 255, 255, 0.1)'}
                       backgroundColor="rgba(0,0,0,0)"
                       width={1000}
                       height={600}
                     />
                   ) : (
                     <div className="flex items-center justify-center h-full text-neon-cyan font-mono animate-pulse uppercase tracking-[0.4em]">
                        Mapping knowledge network...
                     </div>
                   )}
                   
                   <div className="absolute bottom-6 left-6 flex gap-4">
                      <div className="flex items-center gap-2">
                         <div className="w-2 h-2 rounded-full bg-cyber-magenta" />
                         <span className="text-[10px] font-mono text-gray-500 uppercase">System Control</span>
                      </div>
                      <div className="flex items-center gap-2">
                         <div className="w-2 h-2 rounded-full bg-neon-cyan" />
                         <span className="text-[10px] font-mono text-gray-500 uppercase">Knowledge Domain</span>
                      </div>
                   </div>

                   {/* Isomorph Insight Overlay */}
                   <div className="absolute top-6 right-6 w-80 space-y-4">
                      {isomorphs.map((iso, idx) => (
                        <motion.div 
                          key={idx}
                          initial={{ opacity: 0, x: 20 }}
                          animate={{ opacity: 1, x: 0 }}
                          transition={{ delay: 1 + idx * 0.5 }}
                        >
                          <GlassPanel className="bg-amber-500/10 border-amber-500/30 p-4 shadow-[0_0_20px_rgba(242,169,0,0.15)]">
                             <div className="flex items-center gap-2 text-amber-400 mb-2 font-bold text-[10px] tracking-widest uppercase">
                                <Zap size={14} />
                                Cognitive Isomorphism Detected
                             </div>
                             <p className="text-[10px] text-gray-300 leading-relaxed italic mb-3">
                                Structural resonance found between <strong>{iso.domains[0]}</strong> and <strong>{iso.domains[1]}</strong>.
                             </p>
                             <div className="flex flex-wrap gap-1">
                                {iso.shared_concepts.map((c: string) => (
                                  <span key={c} className="px-1.5 py-0.5 rounded bg-amber-500/20 text-[8px] text-amber-200 uppercase font-mono">
                                    {c}
                                  </span>
                                ))}
                             </div>
                          </GlassPanel>
                        </motion.div>
                      ))}
                   </div>
                </GlassPanel>
             </div>
          )}

          {activeTab === 'journal' && (
             <div className="flex flex-col gap-6 h-full overflow-hidden">
                <div className="text-center mb-6">
                  <h2 className="text-3xl font-black text-neon-cyan tracking-tighter uppercase">Intelligence Ledger</h2>
                  <p className="text-[10px] text-gray-500 font-mono tracking-[0.4em] mt-2 uppercase">Historical record of human-agent cognitive synthesis</p>
                </div>

                <div className="flex-1 overflow-y-auto space-y-4 pb-12 pr-4 custom-scrollbar">
                   {timeline.map((event, idx) => (
                     <motion.div 
                        key={idx}
                        initial={{ opacity: 0, x: -20 }}
                        whileInView={{ opacity: 1, x: 0 }}
                        viewport={{ once: true }}
                        transition={{ delay: idx * 0.05 }}
                     >
                       <GlassPanel className="flex flex-col gap-4 hover:bg-white/10 transition-all border-white/5 hover:border-neon-cyan/20 cursor-default group">
                          <div className="flex items-center justify-between">
                             <div className="flex items-center gap-6">
                                <div className="flex flex-col">
                                   <span className="text-[10px] font-mono text-neon-cyan uppercase tracking-widest">{event.date}</span>
                                   <span className="text-[10px] font-mono text-gray-500">{event.time}</span>
                                </div>
                                <div className="h-8 w-[1px] bg-white/10" />
                                <span className="px-3 py-1 rounded-full bg-white/5 border border-white/10 text-[10px] text-gray-400 uppercase tracking-widest font-mono group-hover:border-neon-cyan/30 transition-colors">
                                  {event.tool}
                                </span>
                             </div>
                             <div className="flex items-center gap-2">
                                <span className="text-[10px] font-mono text-gray-500 uppercase">Agent //</span>
                                <span className="text-[10px] font-mono text-white uppercase tracking-widest font-bold">{event.agent}</span>
                             </div>
                          </div>
                          <p className="text-xs text-gray-300 leading-relaxed italic border-l-2 border-neon-cyan/20 pl-4 group-hover:border-neon-cyan transition-colors">
                             {event.summary}
                          </p>
                       </GlassPanel>
                     </motion.div>
                   ))}
                </div>
             </div>
          )}
        </div>

        <AnimatePresence>
          {isFocusMode && (
            <motion.div 
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="absolute inset-0 z-[60] bg-[#0b0c10]/90 backdrop-blur-3xl flex flex-col items-center justify-center p-8 text-center"
            >
              <div className="absolute top-8 right-8">
                 <button 
                  onClick={() => setIsFocusMode(false)}
                  className="p-4 bg-white/5 hover:bg-white/10 rounded-full border border-white/10 transition-all text-gray-500 hover:text-white"
                 >
                   <Plus className="rotate-45" size={24} />
                 </button>
              </div>

              <motion.div 
                initial={{ scale: 0.9 }}
                animate={{ scale: 1 }}
                className="max-w-2xl w-full relative"
              >
                {/* Breathing Circle Background */}
                <motion.div 
                  animate={{ scale: [1, 1.2, 1], opacity: [0.1, 0.3, 0.1] }}
                  transition={{ duration: 6, repeat: Infinity, ease: "easeInOut" }}
                  className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] rounded-full bg-neon-cyan/20 blur-[100px] -z-10"
                />

                <div className="flex flex-col items-center gap-6 mb-12">
                   <div className="relative">
                      <motion.div 
                        animate={{ rotate: 360 }}
                        transition={{ duration: 20, repeat: Infinity, ease: "linear" }}
                        className="absolute inset-0 border-2 border-dashed border-neon-cyan/30 rounded-full scale-125"
                      />
                      <div className="p-8 rounded-[4rem] bg-neon-cyan/10 border-2 border-neon-cyan shadow-[0_0_50px_rgba(102,252,241,0.2)]">
                         <Target size={80} className="text-neon-cyan" />
                      </div>
                   </div>
                   <div>
                      <h2 className="text-5xl font-black tracking-tighter uppercase mb-2">Focus Locked</h2>
                      <p className="text-xs font-mono text-neon-cyan/60 uppercase tracking-[0.5em]">Nothing else matters but this.</p>
                   </div>
                </div>

                <div className="grid grid-cols-2 gap-4 mb-12">
                   <GlassPanel className="py-8 bg-white/[0.02]">
                      <div className="text-[10px] font-mono text-gray-500 uppercase tracking-widest mb-2">Session Duration</div>
                      <div className="text-4xl font-black text-white">
                        {Math.floor(focusTimer / 60)}:{(focusTimer % 60).toString().padStart(2, '0')}
                      </div>
                   </GlassPanel>
                   <GlassPanel className="py-8 bg-white/[0.02]">
                      <div className="text-[10px] font-mono text-gray-500 uppercase tracking-widest mb-2">Cognitive Load</div>
                      <div className="text-4xl font-black text-cyber-magenta uppercase">High</div>
                   </GlassPanel>
                </div>

                <div className="space-y-4">
                   <p className="text-sm text-gray-400 italic mb-8">"The only way out is through. Stay on path."</p>
                   <button 
                    onClick={() => setIsFocusMode(false)}
                    className="w-full py-4 bg-neon-cyan text-black rounded-2xl font-black uppercase tracking-[0.4em] text-sm shadow-[0_0_30px_rgba(102,252,241,0.4)] hover:scale-[1.02] active:scale-[0.98] transition-all"
                   >
                     Complete Session
                   </button>
                </div>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Console / Web Harness Overlay */}
        <AnimatePresence>
          {consoleVisible && (
            <motion.div 
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: '40%', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="absolute bottom-0 left-0 right-0 z-50 px-4 pb-4"
            >
              <div className="h-full bg-[#0b0c10]/95 backdrop-blur-2xl border-t-2 border-x-2 border-neon-cyan/40 rounded-t-3xl shadow-[0_-20px_50px_rgba(0,0,0,0.8)] flex flex-col overflow-hidden">
                <div className="flex items-center justify-between px-6 py-4 border-b border-white/10 shrink-0">
                   <div className="flex items-center gap-3">
                      <Terminal size={18} className="text-neon-cyan" />
                      <span className="font-mono text-xs uppercase tracking-[0.4em] font-bold">Web Harness // Multi-Agent Console</span>
                   </div>
                   <button onClick={() => setConsoleVisible(false)} className="text-gray-500 hover:text-white transition-colors">
                     <Plus className="rotate-45" size={20} />
                   </button>
                </div>
                
                <div className="flex-1 overflow-y-auto p-6 font-mono text-xs space-y-4 custom-scrollbar">
                   {consoleOutput.length === 0 && (
                     <div className="text-gray-600 italic">READY FOR DIRECTIVE // SYSTEM IDLE...</div>
                   )}
                   {consoleOutput.map((item, idx) => (
                     <div key={idx} className={item.type === 'input' ? 'text-neon-cyan flex gap-2' : item.type === 'error' ? 'text-cyber-magenta' : 'text-gray-300 whitespace-pre-wrap pl-6'}>
                        {item.type === 'input' && <ChevronRight size={14} className="mt-0.5" />}
                        {item.content}
                     </div>
                   ))}
                   {isExecuting && (
                     <div className="text-neon-cyan animate-pulse">EXECUTING PROTOCOL... [⏳]</div>
                   )}
                   <div ref={consoleEndRef} />
                </div>
                
                <div className="p-4 bg-white/5 border-t border-white/10 flex gap-4 shrink-0">
                   <input 
                      type="text" 
                      placeholder="Type directive for @chief-of-staff..."
                      className="flex-1 bg-black/40 border border-white/10 rounded-xl px-4 py-3 focus:outline-none focus:border-neon-cyan/50 font-mono text-xs transition-all"
                      value={consoleCommand}
                      onChange={(e) => setConsoleCommand(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && executeCommand()}
                      disabled={isExecuting}
                   />
                   <button 
                     onClick={executeCommand}
                     disabled={isExecuting}
                     className="px-6 bg-neon-cyan text-black rounded-xl hover:shadow-[0_0_15px_#66fcf1] transition-all disabled:opacity-50"
                   >
                     <Send size={18} />
                   </button>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </main>
    </div>
  );
}

export default App;
