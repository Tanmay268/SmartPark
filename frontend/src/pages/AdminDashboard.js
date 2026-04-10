import React, { useEffect, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from 'recharts';
import api from '../api/axios';
import { connectLiveEvents } from '../api/live';
import { useAuth } from '../context/AuthContext';

export default function AdminDashboard() {
  const { user, logout } = useAuth();
  const [overview, setOverview] = useState(null);
  const [slots, setSlots] = useState([]);
  const [socketStatus, setSocketStatus] = useState(false);
  const [lastEvent, setLastEvent] = useState(null);

  const loadOverview = async () => {
    const [overviewRes, slotsRes] = await Promise.all([
      api.get('/admin/overview'),
      api.get('/slots')
    ]);
    setOverview(overviewRes.data);
    setSlots(slotsRes.data.slots);
  };

  useEffect(() => {
    loadOverview().catch(() => {});
    const disconnect = connectLiveEvents({
      onStatus: ({ connected }) => setSocketStatus(connected),
      onEvent: async (event) => {
        setLastEvent(event);
        await loadOverview();
      }
    });
    return disconnect;
  }, []);

  return (
    <div>
      <nav className="navbar">
        <span className="navbar-brand">SmartPark Ops</span>
        <div className="navbar-user">
          <span>{user?.name}</span>
          <span className={`live-pill ${socketStatus ? 'live-pill-on' : ''}`}>{socketStatus ? 'Live' : 'Offline'}</span>
          <button className="btn btn-outline" onClick={logout}>Logout</button>
        </div>
      </nav>

      <div className="page">
        <div className="hero-grid">
          <div className="card hero-card">
            <div className="eyebrow">Admin command center</div>
            <h1>Scalable multi-level parking visibility with prediction, floor balancing, and emergency readiness.</h1>
            <p>
              Monitor occupancy trends, emergency reservation health, and live system behavior across zones and floors.
            </p>
            <div className="chip-row">
              <span className="chip">Lighting: {overview?.environment?.lightingMode || 'NORMAL'}</span>
              <span className="chip">Projected occupancy: {overview?.predictions?.projectedOccupancy || 0}%</span>
              <span className="chip">Latest event: {lastEvent?.type || 'Waiting'}</span>
            </div>
          </div>

          <div className="card">
            <div className="panel-title">Operational Signals</div>
            <div className="signal-list">
              <div><strong>Likely full</strong><span>{overview?.predictions?.likelyFullInMinutes ? `${overview.predictions.likelyFullInMinutes} min` : 'No immediate saturation'}</span></div>
              <div><strong>Best arrival</strong><span>{overview?.predictions?.bestArrivalTime || 'Now'}</span></div>
              <div><strong>Night mode</strong><span>{overview?.environment?.isNight ? 'Enabled' : 'Disabled'}</span></div>
              <div><strong>Prediction confidence</strong><span>{overview?.predictions?.confidence || 'low'}</span></div>
            </div>
          </div>
        </div>

        <div className="grid-4" style={{ marginBottom: '1.5rem' }}>
          {[
            { label: 'Total Slots', value: overview?.summary?.total || 0, tone: 'var(--text)' },
            { label: 'Available', value: overview?.summary?.free || 0, tone: 'var(--success)' },
            { label: 'Booked', value: overview?.summary?.booked || 0, tone: 'var(--warning)' },
            { label: 'Occupied', value: overview?.summary?.occupied || 0, tone: 'var(--danger)' },
            { label: 'Profits Made', value: `Rs ${overview?.profit || 0}`, tone: 'var(--accent)' }
          ].map((item) => (
            <div className="card stat-card" key={item.label}>
              <div className="stat-value" style={{ color: item.tone }}>{item.value}</div>
              <div className="stat-label">{item.label}</div>
            </div>
          ))}
        </div>

        <div className="grid-3">
          <div className="card chart-card">
            <div className="panel-title">Peak Hours</div>
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={overview?.peakHours || []}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(37, 61, 88, 0.12)" />
                <XAxis dataKey="hour" tick={{ fill: '#55708b', fontSize: 11 }} />
                <YAxis tick={{ fill: '#55708b', fontSize: 11 }} />
                <Tooltip />
                <Line type="monotone" dataKey="occupancy" stroke="#0f766e" strokeWidth={3} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="card chart-card">
            <div className="panel-title">Floor Optimization Score</div>
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={overview?.floorLoad || []}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(37, 61, 88, 0.12)" />
                <XAxis dataKey="floor" tick={{ fill: '#55708b', fontSize: 11 }} />
                <YAxis tick={{ fill: '#55708b', fontSize: 11 }} />
                <Tooltip />
                <Bar dataKey="occupancy" fill="#f59e0b" radius={[10, 10, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="card chart-card">
            <div className="panel-title">Zone Load</div>
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={overview?.zoneLoad || []}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(37, 61, 88, 0.12)" />
                <XAxis dataKey="zone" tick={{ fill: '#55708b', fontSize: 11 }} />
                <YAxis tick={{ fill: '#55708b', fontSize: 11 }} />
                <Tooltip />
                <Bar dataKey="occupancy" fill="#2563eb" radius={[10, 10, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="grid-2" style={{ marginTop: '1.5rem', alignItems: 'start' }}>
          <div className="card">
            <div className="panel-title">Live Booking Feed</div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>User</th>
                    <th>Slot</th>
                    <th>Vehicle</th>
                    <th>Status</th>
                    <th>Created</th>
                  </tr>
                </thead>
                <tbody>
                  {(overview?.bookings || []).map((booking) => (
                    <tr key={booking._id}>
                      <td>{booking.userId?.name}<br /><span className="table-subtext">{booking.userId?.email}</span></td>
                      <td>{booking.slotId?.label}<br /><span className="table-subtext">Zone {booking.slotId?.zone} | L{booking.slotId?.floor}</span></td>
                      <td>{booking.vehicleType}<br /><span className="table-subtext">Detected: {booking.numberPlate || 'Not available'}</span></td>
                      <td><span className={`badge ${booking.status === 'ACTIVE' ? 'badge-occupied' : booking.status === 'COMPLETED' ? 'badge-free' : booking.status === 'EXPIRED' ? 'badge-expired' : booking.status === 'PAYMENT_PENDING' ? 'badge-payment' : 'badge-booked'}`}>{booking.status}</span></td>
                      <td>{new Date(booking.createdAt).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card">
            <div className="panel-title">IoT Slot Readiness</div>
            <div className="slot-grid">
              {slots.slice(0, 24).map((slot) => (
                <div className={`smart-slot smart-slot-${slot.status.toLowerCase()}`} key={slot._id}>
                  <div className="smart-slot-top">
                    <strong>{slot.label}</strong>
                    {slot.reservedFor !== 'none' && <span className="mini-tag">Emergency</span>}
                  </div>
                  <div className="muted-copy">Zone {slot.zone} | Floor {slot.floor}</div>
                  <div className="slot-meta-row">
                    <span>{slot.covered ? 'Covered' : 'Open'}</span>
                    <span>{slot.nearLift ? 'Lift-side' : 'Drive lane'}</span>
                  </div>
                  <span className={`badge badge-${slot.status.toLowerCase()}`}>{slot.status}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
