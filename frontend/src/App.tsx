import { useState, useEffect, useRef } from 'react';
import { 
  Trophy, 
  RefreshCw, 
  Volume2, 
  VolumeX,
  Clock,
  Sparkles
} from 'lucide-react';

// Interfaces
interface GroupStanding {
  group_id: number;
  group_name: string;
  total_points: number;
  rank: number;
  starting_pop_bonus: number;
  final_starting_pop: number;
}

export default function App() {
  // App state
  const [standings, setStandings] = useState<GroupStanding[]>([]);
  
  // UX controls
  const [loading, setLoading] = useState(true);
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null);
  const autoRefresh = true;
  const refreshInterval = 10; // in seconds
  const [soundEnabled, setSoundEnabled] = useState(false);
  const [countdown, setCountdown] = useState(refreshInterval);
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date());
  
  // Change tracking for micro-animations
  const [updatedGroups, setUpdatedGroups] = useState<Record<string, boolean>>({});
  
  // Refs to store previous states
  const prevStandingsRef = useRef<GroupStanding[]>([]);
  
  // Audio context ref
  const audioCtxRef = useRef<AudioContext | null>(null);

  // Initialize data
  const fetchData = async (isManual = false) => {
    if (isManual) setLoading(true);
    try {
      const standingsRes = await fetch('/api/public/icebreaker/standings');
      const standingsData = (await standingsRes.json()) as GroupStanding[];
      
      // Detect updates for standings/points
      if (prevStandingsRef.current.length > 0) {
        const updates: Record<string, boolean> = {};
        let hasChanges = false;
        
        standingsData.forEach(curr => {
          const prev = prevStandingsRef.current.find(p => p.group_id === curr.group_id);
          if (prev && prev.total_points !== curr.total_points) {
            updates[curr.group_name] = true;
            hasChanges = true;
          }
        });
        
        if (hasChanges) {
          setUpdatedGroups(updates);
          playUpdateChime();
          setTimeout(() => setUpdatedGroups({}), 4000); // Clear flash after 4s
        }
      }

      setStandings(standingsData);
      prevStandingsRef.current = standingsData;
      setLastUpdated(new Date());
      setCountdown(refreshInterval);
    } catch (err) {
      console.error('Error fetching data:', err);
    } finally {
      setLoading(false);
    }
  };

  // Play synth sound for updates
  const playUpdateChime = () => {
    if (!soundEnabled) return;
    try {
      if (!audioCtxRef.current) {
        audioCtxRef.current = new (window.AudioContext || (window as any).webkitAudioContext)();
      }
      const ctx = audioCtxRef.current;
      if (ctx.state === 'suspended') {
        ctx.resume();
      }
      
      const now = ctx.currentTime;
      const playTone = (freq: number, start: number, duration: number) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        
        osc.type = 'sine';
        osc.frequency.setValueAtTime(freq, start);
        
        gain.gain.setValueAtTime(0.1, start);
        gain.gain.exponentialRampToValueAtTime(0.001, start + duration);
        
        osc.connect(gain);
        gain.connect(ctx.destination);
        
        osc.start(start);
        osc.stop(start + duration);
      };

      playTone(523.25, now, 0.15); // C5
      playTone(659.25, now + 0.08, 0.15); // E5
      playTone(783.99, now + 0.16, 0.25); // G5
    } catch (e) {
      console.warn('Audio play failed', e);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  useEffect(() => {
    if (!autoRefresh) return;
    
    const timer = setInterval(() => {
      setCountdown(prev => {
        if (prev <= 1) {
          fetchData();
          return refreshInterval;
        }
        return prev - 1;
      });
    }, 1000);

    return () => clearInterval(timer);
  }, [autoRefresh, refreshInterval]);

  useEffect(() => {
    setCountdown(refreshInterval);
  }, [refreshInterval]);

  const toggleSound = () => {
    setSoundEnabled(!soundEnabled);
    if (!soundEnabled) {
      if (!audioCtxRef.current) {
        audioCtxRef.current = new (window.AudioContext || (window as any).webkitAudioContext)();
      }
      audioCtxRef.current.resume();
    }
  };

  const formatTime = (date: Date) => {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  // Find max score for relative scaling of progress bars
  const maxScore = Math.max(...standings.map(s => s.total_points), 1);

  return (
    <div className="relative min-h-screen overflow-x-hidden bg-[#FAF9F5] text-[#2C2B29] flex flex-col justify-center items-center py-6 selection:bg-[#E5B83B] selection:text-slate-950">
      {/* Background radial art meshes */}
      <div className="absolute top-0 left-0 w-96 h-96 bg-indigo-500/5 rounded-full blur-[120px] pointer-events-none" />
      <div className="absolute bottom-0 right-0 w-96 h-96 bg-amber-500/5 rounded-full blur-[120px] pointer-events-none" />

      {/* Main Leaderboard Box */}
      <div className="w-full max-w-xl px-4 z-10">
        
        {/* Header section */}
        <header className="flex flex-col sm:flex-row items-center justify-between gap-4 pb-4 mb-4 border-b border-[#E5E2D8]">
          <div className="flex items-center gap-3">
            <div className="p-2.5 bg-gradient-to-tr from-slate-900 to-[#856D30] rounded-xl shadow-md">
              <Trophy className="w-5.5 h-5.5 text-white" />
            </div>
            <div>
              <div className="flex items-center gap-1.5">
                <span className="px-2 py-0.5 text-[9px] uppercase font-bold tracking-widest bg-slate-900/10 text-slate-800 border border-slate-900/20 rounded">
                  MYLC 2026
                </span>
                <span className="text-[10px] text-[#7C7567] font-semibold flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-ping" />
                  Live Scores
                </span>
              </div>
              <h1 className="text-lg font-bold tracking-tight text-[#1F1E1C] mt-0.5">
                DAY GAMES STANDINGS
              </h1>
            </div>
          </div>

          {/* Quick controls */}
          <div className="flex items-center gap-2">
            {/* Auto refresh status */}
            <div className="text-[10px] font-mono text-[#7C7567] bg-white border border-[#E5E2D8] px-2 py-1.5 rounded-lg flex items-center gap-1.5">
              <Clock className="w-3.5 h-3.5 text-slate-600" />
              <span>Auto: {countdown}s</span>
            </div>

            <button 
              onClick={() => fetchData(true)}
              disabled={loading}
              className="p-2 bg-white border border-[#E5E2D8] text-[#7C7567] hover:text-[#2C2B29] rounded-lg hover:bg-[#EBE8DF] disabled:opacity-50 transition-colors cursor-pointer"
              title="Refresh"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            </button>

            {/* Audio Toggle */}
            <button
              onClick={toggleSound}
              className={`p-2 rounded-lg border transition-all cursor-pointer ${
                soundEnabled 
                  ? 'bg-amber-650/10 text-[#856D30] border-[#E5B83B]/30' 
                  : 'bg-white border-[#E5E2D8] text-[#7C7567] hover:text-[#2C2B29] hover:bg-[#EBE8DF]'
              }`}
              title={soundEnabled ? 'Mute' : 'Unmute'}
            >
              {soundEnabled ? <Volume2 className="w-3.5 h-3.5 animate-bounce" /> : <VolumeX className="w-3.5 h-3.5" />}
            </button>
          </div>
        </header>

        {/* Clean vertical board list */}
        <div className="glass-panel rounded-xl overflow-hidden shadow-md border border-[#E8E5DC]/80 p-4 space-y-1.5">
          {standings.length === 0 ? (
            <div className="text-center text-[#7C7567] text-sm py-10">
              Loading standings data...
            </div>
          ) : (
            standings.map((row) => {
              const isHighlighted = selectedGroup === row.group_name;
              const isJustUpdated = updatedGroups[row.group_name];
              
              // Calculate score line percentage (width)
              const scorePercent = Math.max(3, (row.total_points / maxScore) * 100);

              // Calculate background size to anchor the gradient to the parent width
              // As the bar widens, it uncovers more of the gold at the right
              const bgSize = row.total_points > 0 
                ? `${(maxScore / row.total_points) * 100}% 100%` 
                : "100% 100%";

              return (
                <div 
                  key={row.group_id}
                  onClick={() => setSelectedGroup(isHighlighted ? null : row.group_name)}
                  className={`flex flex-col gap-1 py-1.5 px-3 rounded-lg cursor-pointer transition-all duration-200 ${
                    isHighlighted 
                      ? 'bg-[#EFECE3] border border-[#E5B83B]/20 shadow-sm' 
                      : 'hover:bg-[#F2EFE6]/50 border border-transparent'
                  } ${isJustUpdated ? 'bg-amber-500/10 animate-pulse' : ''}`}
                >
                  {/* Top line: Rank, Name, Points */}
                  <div className="flex items-center justify-between text-xs sm:text-sm">
                    <div className="flex items-center gap-3">
                      <span className={`inline-flex items-center justify-center w-5.5 h-5.5 rounded text-[10px] font-black ${
                        row.rank === 1 
                          ? 'bg-amber-500/15 text-amber-800' 
                          : row.rank === 2
                          ? 'bg-slate-400/20 text-slate-700'
                          : row.rank === 3
                          ? 'bg-orange-500/15 text-orange-700'
                          : 'bg-[#EBE8DF] text-[#7C7567]'
                      }`}>
                        {row.rank}
                      </span>
                      <span className={`font-semibold transition-colors ${
                        isHighlighted ? 'text-[#1F1E1C] font-bold' : 'text-[#3E3C38]'
                      }`}>
                        {row.group_name}
                      </span>
                      {isJustUpdated && (
                        <Sparkles className="w-3.5 h-3.5 text-amber-500 animate-spin" />
                      )}
                    </div>
                    
                    {/* Numerical Score */}
                    <div className="font-mono font-bold text-sm sm:text-base text-right text-[#856D30]">
                      {row.total_points.toLocaleString()} <span className="text-[9px] text-[#7C7567] font-sans font-normal uppercase tracking-wider ml-0.5">pts</span>
                    </div>
                  </div>

                  {/* Bottom line: The thicker visual representation line */}
                  <div className="w-full bg-[#E5E2D8] rounded-md h-3.5 overflow-hidden">
                    <div 
                      className="h-full rounded-md transition-all duration-500 ease-out"
                      style={{ 
                        width: `${scorePercent}%`,
                        backgroundImage: 'linear-gradient(to right, #1F1E1C 0%, #856D30 65%, #E5B83B 100%)',
                        backgroundSize: bgSize,
                        backgroundPosition: 'left center',
                        boxShadow: isHighlighted ? '0 0 6px rgba(229, 184, 59, 0.4)' : undefined
                      }}
                    />
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Footer info stamp */}
        <footer className="mt-4 text-center text-[9px] text-[#7C7567] font-semibold">
          Last updated: {formatTime(lastUpdated)} • Overall Leader: {standings[0]?.group_name || 'N/A'}
        </footer>

      </div>
    </div>
  );
}
