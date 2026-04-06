/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { 
  Search, 
  Calendar, 
  User, 
  Bell, 
  ArrowRight, 
  ArrowLeftRight, 
  LayoutList, 
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  RotateCcw,
  Activity,
  Database,
  ShieldCheck,
  Plane
} from 'lucide-react';

// --- Types ---

type View = 'home' | 'results';

interface FlightResult {
  id: string;
  date: string;
  route: string;
  lastSeen: string;
  flightNumber: string;
  departs: string;
  arrives: string;
  economy: string | null;
  premium: string | null;
  business: string | null;
  first: string | null;
  airlineLogo: string;
}

// --- Mock Data ---

const MOCK_RESULTS: FlightResult[] = [
  {
    id: '1',
    date: 'Oct 12, Sat',
    route: 'YYZ → JFK',
    lastSeen: '2m ago',
    flightNumber: 'UA 421',
    departs: '06:45',
    arrives: '08:12',
    economy: '30.0k',
    premium: '45.5k',
    business: null,
    first: null,
    airlineLogo: 'https://picsum.photos/seed/ua1/32/32'
  },
  {
    id: '2',
    date: 'Oct 12, Sat',
    route: 'YYZ → EWR → JFK',
    lastSeen: '14m ago',
    flightNumber: 'UA 1102',
    departs: '11:30',
    arrives: '15:55',
    economy: '22.5k',
    premium: null,
    business: '60.0k',
    first: null,
    airlineLogo: 'https://picsum.photos/seed/ua2/32/32'
  },
  {
    id: '3',
    date: 'Oct 13, Sun',
    route: 'YYZ → JFK',
    lastSeen: '34s ago',
    flightNumber: 'UA 882',
    departs: '09:00',
    arrives: '10:35',
    economy: '30.0k',
    premium: null,
    business: '80.0k',
    first: '110k',
    airlineLogo: 'https://picsum.photos/seed/ua3/32/32'
  },
  {
    id: '4',
    date: 'Oct 14, Mon',
    route: 'YYZ → JFK',
    lastSeen: '1h ago',
    flightNumber: 'UA 204',
    departs: '18:20',
    arrives: '19:48',
    economy: '30.0k',
    premium: null,
    business: null,
    first: null,
    airlineLogo: 'https://picsum.photos/seed/ua4/32/32'
  },
  {
    id: '5',
    date: 'Oct 15, Tue',
    route: 'YYZ → JFK',
    lastSeen: '5m ago',
    flightNumber: 'UA 990',
    departs: '06:00',
    arrives: '07:28',
    economy: '30.0k',
    premium: '45.0k',
    business: '85.0k',
    first: null,
    airlineLogo: 'https://picsum.photos/seed/ua5/32/32'
  }
];

// --- Components ---

const Navbar = ({ currentView, setView }: { currentView: View, setView: (v: View) => void }) => (
  <nav className="fixed top-0 w-full z-50 glass-nav shadow-2xl shadow-black/50 flex justify-between items-center px-6 py-3 font-sans tracking-tight">
    <div className="flex items-center gap-8">
      <span 
        className="text-xl font-black tracking-tighter text-orange-500 uppercase cursor-pointer"
        onClick={() => setView('home')}
      >
        Aviation Ledger
      </span>
      <div className="hidden md:flex gap-6 items-center">
        <button 
          onClick={() => setView('home')}
          className={`text-sm transition-colors ${currentView === 'home' ? 'text-orange-500 font-bold border-b-2 border-orange-500 pb-1' : 'text-zinc-400 hover:text-zinc-100'}`}
        >
          Search
        </button>
        <button className="text-zinc-400 hover:text-zinc-100 transition-colors text-sm">My Bookings</button>
        <button className="text-zinc-400 hover:text-zinc-100 transition-colors text-sm">Fleet</button>
        <button className="text-zinc-400 hover:text-zinc-100 transition-colors text-sm">Insights</button>
      </div>
    </div>
    <div className="flex items-center gap-4">
      <button className="p-2 text-zinc-400 hover:bg-zinc-800/50 transition-all duration-200 rounded-lg active:scale-95">
        <Bell size={20} />
      </button>
      <button className="p-2 text-zinc-400 hover:bg-zinc-800/50 transition-all duration-200 rounded-lg active:scale-95">
        <User size={20} />
      </button>
    </div>
  </nav>
);

const Footer = () => (
  <footer className="bg-zinc-950 w-full py-8 mt-auto border-t border-zinc-800/30">
    <div className="flex flex-col md:flex-row justify-between items-center px-8 max-w-7xl mx-auto gap-4">
      <div className="flex flex-col gap-1">
        <span className="text-sm font-bold text-zinc-300">Aviation Ledger</span>
        <span className="text-[10px] text-zinc-500 tracking-wide uppercase">© 2024 Aviation Ledger. Precision flight data systems.</span>
      </div>
      <div className="flex gap-8">
        <a className="text-[10px] text-zinc-500 tracking-wide uppercase hover:text-orange-400 transition-colors" href="#">Privacy Policy</a>
        <a className="text-[10px] text-zinc-500 tracking-wide uppercase hover:text-orange-400 transition-colors" href="#">Terms of Service</a>
        <a className="text-[10px] text-zinc-500 tracking-wide uppercase hover:text-orange-400 transition-colors" href="#">API Access</a>
        <a className="text-[10px] text-zinc-500 tracking-wide uppercase hover:text-orange-400 transition-colors" href="#">Support</a>
      </div>
    </div>
  </footer>
);

const HomeView = ({ onSearch }: { onSearch: () => void }) => (
  <div className="flex-grow pt-32 pb-20 px-6">
    <section className="max-w-4xl mx-auto text-center mb-16">
      <h1 className="text-6xl md:text-8xl font-black tracking-tighter text-on-surface mb-4 uppercase italic">
        SEATAERO
      </h1>
      <p className="text-on-surface-variant text-lg md:text-xl tracking-tight font-light">
        Search United award flight availability with precision ledger data.
      </p>
    </section>

    <section className="max-w-5xl mx-auto">
      <div className="glass-card p-1 rounded-xl shadow-2xl shadow-black/80">
        <div className="bg-surface-container-low rounded-lg p-6 md:p-10">
          <div className="grid grid-cols-1 md:grid-cols-[1fr_auto_1fr] items-center gap-4 mb-8">
            <div className="group relative bg-surface-container-lowest border border-outline/15 focus-within:border-primary transition-all p-4 rounded-lg">
              <label className="block text-[10px] uppercase tracking-widest text-on-surface-variant mb-1 font-bold">Origin</label>
              <input 
                className="bg-transparent border-none p-0 w-full text-4xl font-black tabular-nums tracking-tighter text-on-surface focus:ring-0 uppercase" 
                defaultValue="YYZ"
                type="text" 
              />
              <span className="text-xs text-on-surface-variant/60 font-medium">Toronto Pearson Intl</span>
            </div>
            
            <button className="z-10 bg-surface-container-high hover:bg-surface-container-highest w-12 h-12 rounded-full flex items-center justify-center border border-outline/15 transition-all active:scale-90 mx-auto md:-mx-4 shadow-xl">
              <ArrowLeftRight className="text-primary" size={20} />
            </button>

            <div className="group relative bg-surface-container-lowest border border-outline/15 focus-within:border-primary transition-all p-4 rounded-lg text-right md:text-left">
              <label className="block text-[10px] uppercase tracking-widest text-on-surface-variant mb-1 font-bold">Destination</label>
              <input 
                className="bg-transparent border-none p-0 w-full text-4xl font-black tabular-nums tracking-tighter text-on-surface focus:ring-0 uppercase text-right md:text-left" 
                defaultValue="JFK"
                type="text" 
              />
              <span className="text-xs text-on-surface-variant/60 font-medium">John F. Kennedy Intl</span>
            </div>
          </div>

          <div className="flex flex-col md:flex-row gap-4 items-stretch">
            <div className="flex-1 grid grid-cols-2 gap-4">
              <div className="bg-surface-container-lowest border border-outline/15 p-4 rounded-lg">
                <label className="block text-[10px] uppercase tracking-widest text-on-surface-variant mb-1 font-bold">Date Range</label>
                <div className="flex items-center gap-2 text-on-surface font-medium">
                  <Calendar size={14} className="text-on-surface-variant" />
                  <span className="tabular-nums">Oct 24 - Nov 12</span>
                </div>
              </div>
              <div className="bg-surface-container-lowest border border-outline/15 p-4 rounded-lg">
                <label className="block text-[10px] uppercase tracking-widest text-on-surface-variant mb-1 font-bold">Cabin Class</label>
                <div className="flex items-center gap-2 text-on-surface font-medium">
                  <LayoutList size={14} className="text-on-surface-variant" />
                  <span>Business / First</span>
                </div>
              </div>
            </div>
            <button 
              onClick={onSearch}
              className="aviation-gradient px-12 py-4 rounded-lg text-on-primary-container font-black uppercase tracking-widest text-sm hover:brightness-110 transition-all shadow-lg shadow-primary-container/20 flex items-center justify-center gap-2"
            >
              Search Awards
              <ArrowRight size={18} />
            </button>
          </div>
        </div>
      </div>
    </section>

    <section className="max-w-5xl mx-auto mt-12 grid grid-cols-1 md:grid-cols-3 gap-4">
      <div className="bg-surface-container-low p-6 rounded-lg border border-outline/5">
        <div className="flex items-center justify-between mb-4">
          <Activity className="text-primary" size={20} />
          <span className="text-[10px] bg-secondary-container/20 text-secondary px-2 py-0.5 rounded-full font-bold uppercase tracking-wider">Live</span>
        </div>
        <h3 className="text-on-surface-variant text-[10px] uppercase tracking-widest font-bold mb-2">Network Latency</h3>
        <p className="text-3xl font-black tabular-nums">42<span className="text-sm font-normal text-on-surface-variant/50 ml-1">MS</span></p>
      </div>
      <div className="bg-surface-container-low p-6 rounded-lg border border-outline/5">
        <div className="flex items-center justify-between mb-4">
          <Database className="text-primary" size={20} />
        </div>
        <h3 className="text-on-surface-variant text-[10px] uppercase tracking-widest font-bold mb-2">Cached Routes</h3>
        <p className="text-3xl font-black tabular-nums">1,204<span className="text-sm font-normal text-on-surface-variant/50 ml-1">PTRS</span></p>
      </div>
      <div className="bg-surface-container-low p-6 rounded-lg border border-outline/5">
        <div className="flex items-center justify-between mb-4">
          <ShieldCheck className="text-primary" size={20} />
        </div>
        <h3 className="text-on-surface-variant text-[10px] uppercase tracking-widest font-bold mb-2">Data Integrity</h3>
        <p className="text-3xl font-black tabular-nums">99.9<span className="text-sm font-normal text-on-surface-variant/50 ml-1">%</span></p>
      </div>
    </section>
  </div>
);

const ResultsView = () => {
  const [directOnly, setDirectOnly] = useState(true);

  return (
    <div className="pt-20 pb-12 px-6 max-w-[1600px] mx-auto min-h-screen flex flex-col">
      <div className="sticky top-16 z-40 py-4 mb-6">
        <div className="bg-surface-container-low p-3 rounded-lg flex flex-col lg:flex-row items-center gap-4 shadow-xl border border-outline-variant/10">
          <div className="flex items-center gap-2 flex-1 w-full">
            <div className="flex items-center gap-3 bg-surface-container-lowest px-4 py-2 rounded border border-outline-variant/15 flex-1">
              <span className="text-primary text-xs font-bold uppercase tracking-widest">YYZ</span>
              <ArrowRight size={14} className="text-zinc-600" />
              <span className="text-primary text-xs font-bold uppercase tracking-widest">JFK</span>
            </div>
            <div className="bg-surface-container-lowest px-4 py-2 rounded border border-outline-variant/15 text-sm font-medium text-on-surface-variant flex items-center gap-2 min-w-[140px]">
              <Calendar size={14} className="text-zinc-500" />
              Oct 12 - Oct 19
            </div>
            <div className="bg-surface-container-lowest px-4 py-2 rounded border border-outline-variant/15 text-sm font-medium text-on-surface-variant flex items-center gap-2">
              <User size={14} className="text-zinc-500" />
              1
            </div>
          </div>
          <div className="flex items-center gap-1 bg-surface-container-lowest p-1 rounded-md border border-outline-variant/15">
            <button className="px-4 py-1.5 text-[10px] font-bold rounded flex items-center gap-2 transition-all text-zinc-500 hover:text-zinc-300 uppercase tracking-wider">
              <CalendarDays size={14} />
              Calendar
            </button>
            <button className="px-4 py-1.5 text-[10px] font-bold rounded flex items-center gap-2 transition-all bg-primary-container text-on-primary-container shadow-lg shadow-primary-container/20 uppercase tracking-wider">
              <LayoutList size={14} />
              List
            </button>
          </div>
        </div>
      </div>

      <div className="mb-8 grid grid-cols-1 md:grid-cols-4 gap-6 items-end">
        <div className="space-y-3">
          <label className="text-[10px] uppercase tracking-[0.2em] text-on-surface-variant font-bold">Cabin Class</label>
          <div className="flex flex-wrap gap-3">
            {['Economy', 'Business', 'First'].map(cabin => (
              <label key={cabin} className="flex items-center gap-2 cursor-pointer group">
                <input 
                  type="checkbox" 
                  defaultChecked={cabin !== 'First'}
                  className="w-4 h-4 rounded border-outline-variant bg-surface-container-lowest text-primary-container focus:ring-primary-container/20" 
                />
                <span className="text-xs text-on-surface-variant group-hover:text-on-surface transition-colors">{cabin}</span>
              </label>
            ))}
          </div>
        </div>
        
        <div className="space-y-3">
          <div className="flex justify-between items-center">
            <label className="text-[10px] uppercase tracking-[0.2em] text-on-surface-variant font-bold">Max Miles</label>
            <span className="text-xs font-mono text-primary tabular-nums">120k</span>
          </div>
          <input 
            type="range" 
            className="w-full h-1 bg-surface-container-high rounded-lg appearance-none cursor-pointer accent-primary-container" 
          />
        </div>

        <div className="flex items-center gap-3 pb-1">
          <button 
            onClick={() => setDirectOnly(!directOnly)}
            className={`relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none ${directOnly ? 'bg-primary-container' : 'bg-zinc-700'}`}
          >
            <span className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${directOnly ? 'translate-x-4' : 'translate-x-0'}`} />
          </button>
          <span className="text-xs text-on-surface-variant font-medium">Direct flights only</span>
        </div>

        <div className="flex justify-end">
          <button className="text-[10px] font-bold text-primary flex items-center gap-1 hover:underline uppercase tracking-widest">
            <RotateCcw size={14} />
            Reset all filters
          </button>
        </div>
      </div>

      <div className="flex-1 bg-surface-container-low rounded-lg overflow-hidden border border-outline-variant/10 shadow-2xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-surface-container-high/50 text-[10px] uppercase tracking-[0.15em] text-on-surface-variant border-b border-outline-variant/10">
                <th className="py-4 px-6 font-black">Date</th>
                <th className="py-4 px-2 font-black">Last Seen</th>
                <th className="py-4 px-4 font-black">Flight</th>
                <th className="py-4 px-4 font-black">Departs</th>
                <th className="py-4 px-4 font-black">Arrives</th>
                <th className="py-4 px-4 font-black text-center">Economy</th>
                <th className="py-4 px-4 font-black text-center">Premium</th>
                <th className="py-4 px-4 font-black text-center">Business</th>
                <th className="py-4 px-4 font-black text-center pr-6">First</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-outline-variant/5">
              {MOCK_RESULTS.map((row, idx) => (
                <tr key={row.id} className={`hover:bg-surface-container-highest/30 transition-colors group ${idx % 2 === 1 ? 'bg-surface-container-low/40' : ''}`}>
                  <td className="py-4 px-6">
                    <div className="flex flex-col">
                      <span className="text-sm font-bold text-on-surface">{row.date}</span>
                      <span className="text-[10px] text-zinc-500 tabular-nums">{row.route}</span>
                    </div>
                  </td>
                  <td className="py-4 px-2">
                    <span className="text-[10px] font-medium text-zinc-500 tabular-nums">{row.lastSeen}</span>
                  </td>
                  <td className="py-4 px-4">
                    <div className="flex items-center gap-3">
                      <div className="w-6 h-6 rounded bg-zinc-800 flex items-center justify-center overflow-hidden">
                        <img src={row.airlineLogo} alt="Airline" className="w-full h-full object-cover" />
                      </div>
                      <span className="text-xs font-bold text-on-surface tracking-tight">{row.flightNumber}</span>
                    </div>
                  </td>
                  <td className="py-4 px-4">
                    <div className="flex flex-col">
                      <span className="text-sm font-black text-on-surface tabular-nums">{row.departs}</span>
                      <span className="text-[10px] text-zinc-500 uppercase">YYZ</span>
                    </div>
                  </td>
                  <td className="py-4 px-4">
                    <div className="flex flex-col">
                      <span className="text-sm font-black text-on-surface tabular-nums">{row.arrives}</span>
                      <span className="text-[10px] text-zinc-500 uppercase">JFK</span>
                    </div>
                  </td>
                  <td className="py-4 px-4 text-center">
                    {row.economy ? (
                      <span className="inline-block px-3 py-1 rounded-lg text-[11px] font-black bg-secondary/10 text-secondary border border-secondary/20 shadow-[0_0_12px_rgba(98,223,125,0.1)]">
                        {row.economy}
                      </span>
                    ) : <span className="text-zinc-700 font-bold">—</span>}
                  </td>
                  <td className="py-4 px-4 text-center">
                    {row.premium ? (
                      <span className="inline-block px-3 py-1 rounded-lg text-[11px] font-black bg-primary-container/10 text-primary-container border border-primary-container/20">
                        {row.premium}
                      </span>
                    ) : <span className="text-zinc-700 font-bold">—</span>}
                  </td>
                  <td className="py-4 px-4 text-center">
                    {row.business ? (
                      <span className="inline-block px-3 py-1 rounded-lg text-[11px] font-black bg-secondary/10 text-secondary border border-secondary/20 shadow-[0_0_12px_rgba(98,223,125,0.1)]">
                        {row.business}
                      </span>
                    ) : <span className="text-zinc-700 font-bold">—</span>}
                  </td>
                  <td className="py-4 px-4 text-center pr-6">
                    {row.first ? (
                      <span className="inline-block px-3 py-1 rounded-lg text-[11px] font-black bg-primary-container/10 text-primary-container border border-primary-container/20">
                        {row.first}
                      </span>
                    ) : <span className="text-zinc-700 font-bold">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        
        <div className="bg-surface-container-high px-6 py-4 flex justify-between items-center text-[10px] font-bold text-on-surface-variant uppercase tracking-widest">
          <div className="flex gap-6">
            <span>Showing 154 Results</span>
            <span className="text-secondary">42 Saver Awards Found</span>
          </div>
          <div className="flex gap-4 items-center">
            <span>Page 1 of 8</span>
            <div className="flex gap-1">
              <button className="w-6 h-6 rounded bg-zinc-800 flex items-center justify-center hover:bg-zinc-700 transition-colors">
                <ChevronLeft size={14} />
              </button>
              <button className="w-6 h-6 rounded bg-zinc-800 flex items-center justify-center hover:bg-zinc-700 transition-colors">
                <ChevronRight size={14} />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

// --- Main App ---

export default function App() {
  const [view, setView] = useState<View>('home');

  return (
    <div className="min-h-screen flex flex-col selection:bg-primary-container/30">
      <Navbar currentView={view} setView={setView} />
      
      <main className="flex-grow">
        <AnimatePresence mode="wait">
          {view === 'home' ? (
            <motion.div 
              key="home"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
            >
              <HomeView onSearch={() => setView('results')} />
            </motion.div>
          ) : (
            <motion.div 
              key="results"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
            >
              <ResultsView />
            </motion.div>
          )}
        </AnimatePresence>
      </main>

      <Footer />
    </div>
  );
}
