import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api/axios';
import { connectLiveEvents } from '../api/live';
import CameraCapture from '../components/CameraCapture';
import { useAuth } from '../context/AuthContext';

const VEHICLES = [
  { value: 'car', label: 'Car' },
  { value: 'bike', label: 'Bike' },
  { value: 'truck', label: 'Truck' },
  { value: 'ambulance', label: 'Ambulance' },
  { value: 'fire_truck', label: 'Fire Truck' },
  { value: 'police', label: 'Police' }
];

function bookingBadge(status) {
  if (status === 'ACTIVE') return 'badge-occupied';
  if (status === 'COMPLETED') return 'badge-free';
  if (status === 'EXPIRED') return 'badge-expired';
  if (status === 'PAYMENT_PENDING') return 'badge-payment';
  return 'badge-booked';
}

export default function Dashboard() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [slots, setSlots] = useState([]);
  const [summary, setSummary] = useState({});
  const [bookings, setBookings] = useState([]);
  const [context, setContext] = useState(null);
  const [recommendation, setRecommendation] = useState(null);
  const [lastEvent, setLastEvent] = useState(null);
  const [socketStatus, setSocketStatus] = useState(false);
  const [gateMessage, setGateMessage] = useState('');
  const [form, setForm] = useState({
    vehicleType: 'car',
    durationHours: 1,
    numberPlate: '',
    preference: { covered: false, shaded: false, nearLift: false }
  });
  const [message, setMessage] = useState('');
  const [captureMessage, setCaptureMessage] = useState('');
  const [captureBusy, setCaptureBusy] = useState(false);

  const loadDashboard = async () => {
    const [slotsRes, bookingsRes, contextRes] = await Promise.all([
      api.get('/slots'),
      api.get('/bookings/my'),
      api.get('/parking/context')
    ]);
    setSlots(slotsRes.data.slots);
    setSummary(slotsRes.data.summary);
    setBookings(bookingsRes.data.bookings);
    setContext(contextRes.data);
  };

  const requestRecommendation = async () => {
    try {
      const res = await api.post('/parking/recommendation', {
        vehicleType: form.vehicleType,
        preference: form.preference
      });
      setRecommendation(res.data);
      setMessage('');
    } catch (error) {
      setMessage(error.response?.data?.message || 'Unable to compute recommendation');
    }
  };

  const createBooking = async () => {
    try {
      if (!form.numberPlate.trim()) {
        setMessage('Capture or enter a number plate before booking');
        return;
      }
      const res = await api.post('/bookings', form);
      setMessage('Booking confirmed with QR access.');
      setRecommendation({
        slot: {
          _id: res.data.booking.slotId?._id,
          label: res.data.booking.slotId?.label,
          zone: res.data.booking.slotId?.zone,
          floor: res.data.booking.slotId?.floor
        },
        weather: res.data.booking.weatherSnapshot,
        recommendation: res.data.booking.recommendation,
        navigation: res.data.booking.navigation
      });
      await loadDashboard();
    } catch (error) {
      setMessage(error.response?.data?.message || 'Booking failed');
    }
  };

  const handleCapturedPlate = async (file) => {
    setCaptureBusy(true);
    setCaptureMessage('Uploading captured image for OCR...');
    setMessage('');
    try {
      const data = new FormData();
      data.append('plate_image', file);
      const res = await api.post('/capture-plate', data, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });
      const detectedPlate = res.data.plate || '';
      setForm((current) => ({
        ...current,
        numberPlate: detectedPlate,
        vehicleType: res.data.vehicle?.type || current.vehicleType
      }));
      setCaptureMessage(`Plate detected: ${detectedPlate}`);
    } catch (error) {
      setCaptureMessage(error.response?.data?.message || 'Image capture upload failed');
    } finally {
      setCaptureBusy(false);
    }
  };

  const updateBooking = async (bookingId, action) => {
    await api.post(`/bookings/${bookingId}/${action}`);
    await loadDashboard();
  };

  const simulateQrEntry = async (qrToken) => {
    try {
      const res = await api.post('/gate/scan-qr', { qrToken });
      setGateMessage(res.data.message);
      await loadDashboard();
    } catch (error) {
      setGateMessage(error.response?.data?.message || 'QR validation failed');
    }
  };

  useEffect(() => {
    loadDashboard().catch(() => {});
    requestRecommendation().catch(() => {});

    const disconnect = connectLiveEvents({
      onStatus: ({ connected }) => setSocketStatus(connected),
      onEvent: async (event) => {
        setLastEvent(event);
        await loadDashboard();
      }
    });

    return disconnect;
  }, []);

  useEffect(() => {
    const paymentBooking = bookings.find((booking) => booking.status === 'PAYMENT_PENDING');
    if (paymentBooking) {
      navigate(`/payment/${paymentBooking._id}`);
    }
  }, [bookings, navigate]);

  const groupedSlots = useMemo(() => {
    return slots.reduce((acc, slot) => {
      const key = `Floor ${slot.floor}`;
      acc[key] = acc[key] || [];
      acc[key].push(slot);
      return acc;
    }, {});
  }, [slots]);

  return (
    <div>
      <nav className="navbar">
        <span className="navbar-brand">SmartPark Control</span>
        <div className="navbar-user">
          <span>{user?.name}</span>
          <span className={`live-pill ${socketStatus ? 'live-pill-on' : ''}`}>{socketStatus ? 'Live' : 'Offline'}</span>
          <button className="btn btn-outline" onClick={logout}>Logout</button>
        </div>
      </nav>

      <div className="page">
        <div className="hero-grid">
          <div className="card hero-card">
            <div className="eyebrow">Environment-aware operations</div>
            <h1>Real-time parking allocation with weather, floor scoring, and QR entry.</h1>
            <p>
              The system now reacts to occupancy, emergency class, weather, and user preferences before assigning a slot.
            </p>
            <div className="chip-row">
              <span className="chip">Lighting: {context?.environment?.lightingMode || 'NORMAL'}</span>
              <span className="chip">Occupancy: {summary.occupancyRate || 0}%</span>
              <span className="chip">Prediction: {context?.predictions?.likelyFullInMinutes ? `Full in ${context.predictions.likelyFullInMinutes} min` : 'Capacity stable'}</span>
            </div>
          </div>

          <div className="card">
            <div className="panel-title">Live System Signals</div>
            <div className="signal-list">
              <div><strong>Night mode</strong><span>{context?.environment?.isNight ? 'Lighting boosted' : 'Day profile'}</span></div>
              <div><strong>Best arrival</strong><span>{context?.predictions?.bestArrivalTime || 'Now'}</span></div>
              <div><strong>Projected occupancy</strong><span>{context?.predictions?.projectedOccupancy || 0}%</span></div>
              <div><strong>Latest event</strong><span>{lastEvent ? lastEvent.type : 'Waiting for live updates'}</span></div>
            </div>
          </div>
        </div>

        <div className="grid-4" style={{ marginBottom: '1.5rem' }}>
          {[
            { label: 'Total Slots', value: summary.total || 0, tone: 'var(--text)' },
            { label: 'Available', value: summary.free || 0, tone: 'var(--success)' },
            { label: 'Booked', value: summary.booked || 0, tone: 'var(--warning)' },
            { label: 'Occupied', value: summary.occupied || 0, tone: 'var(--danger)' }
          ].map((item) => (
            <div className="card stat-card" key={item.label}>
              <div className="stat-value" style={{ color: item.tone }}>{item.value}</div>
              <div className="stat-label">{item.label}</div>
            </div>
          ))}
        </div>

        <div className="grid-2" style={{ alignItems: 'start' }}>
          <div className="card">
            <div className="panel-title">Smart Booking</div>
            <div className="form-group">
              <label>Vehicle Type</label>
              <select
                className="input"
                value={form.vehicleType}
                onChange={(e) => setForm((current) => ({ ...current, vehicleType: e.target.value }))}
              >
                {VEHICLES.map((vehicle) => (
                  <option key={vehicle.value} value={vehicle.value}>{vehicle.label}</option>
                ))}
              </select>
            </div>
            <div className="form-group">
              <label>Number Plate</label>
              <input
                className="input"
                value={form.numberPlate}
                onChange={(e) => setForm((current) => ({ ...current, numberPlate: e.target.value }))}
                placeholder="MH12AB1234"
              />
            </div>
            <CameraCapture
              label="Number Plate Camera"
              buttonText={captureBusy ? 'Processing...' : 'Capture Plate'}
              onCapture={handleCapturedPlate}
            />
            {captureMessage && <div className={`alert ${captureMessage.startsWith('Plate detected:') ? 'alert-success' : 'alert-error'}`}>{captureMessage}</div>}
            <div className="form-group">
              <label>Duration (hours)</label>
              <input
                className="input"
                type="number"
                min={1}
                max={24}
                value={form.durationHours}
                onChange={(e) => setForm((current) => ({ ...current, durationHours: Number(e.target.value) || 1 }))}
              />
            </div>

            <div className="preference-box">
              <div className="panel-subtitle">User Preferences</div>
              {[
                ['covered', 'Covered slot'],
                ['shaded', 'Shaded slot'],
                ['nearLift', 'Near lift']
              ].map(([key, label]) => (
                <label className="check-row" key={key}>
                  <input
                    type="checkbox"
                    checked={form.preference[key]}
                    onChange={(e) => setForm((current) => ({
                      ...current,
                      preference: { ...current.preference, [key]: e.target.checked }
                    }))}
                  />
                  <span>{label}</span>
                </label>
              ))}
            </div>

            {message && <div className={`alert ${message.toLowerCase().includes('failed') ? 'alert-error' : 'alert-success'}`}>{message}</div>}

            <div className="stack-mobile">
              <button className="btn btn-outline" onClick={requestRecommendation}>Refresh Recommendation</button>
              <button className="btn btn-primary" onClick={createBooking}>Book Best Slot</button>
            </div>
          </div>

          <div className="card">
            <div className="panel-title">Allocation Decision</div>
            {recommendation ? (
              <>
                <div className="recommendation-shell">
                  <div>
                    <div className="eyebrow">Recommended Slot</div>
                    <div className="recommendation-label">{recommendation.slot?.label || '-'}</div>
                    <div className="recommendation-meta">
                      Zone {recommendation.slot?.zone} | Level {recommendation.slot?.floor}
                    </div>
                  </div>
                  <span className="badge badge-booked">{recommendation.weather?.category || 'normal'}</span>
                </div>
                <p className="recommendation-copy">{recommendation.recommendation?.reason}</p>
                <div className="signal-list">
                  <div><strong>Floor score</strong><span>{Math.round(recommendation.recommendation?.floorScore || 0)}</span></div>
                  <div><strong>Slot score</strong><span>{Math.round(recommendation.recommendation?.slotScore || 0)}</span></div>
                  <div><strong>Weather</strong><span>{recommendation.weather?.description || 'clear'} / {recommendation.weather?.temperatureC || 28} C</span></div>
                </div>
                <div className="panel-subtitle" style={{ marginTop: '1rem' }}>Navigation</div>
                <ol className="route-list">
                  {(recommendation.navigation?.steps || []).map((step, index) => (
                    <li key={`${step}-${index}`}>{step}</li>
                  ))}
                </ol>
              </>
            ) : (
              <p className="muted-copy">Request a recommendation to see floor selection, weather-aware weighting, and navigation.</p>
            )}
          </div>
        </div>

        <div className="card" style={{ marginTop: '1.5rem' }}>
          <div className="panel-title">My Active and Recent Bookings</div>
          {gateMessage && <div className="alert alert-success">{gateMessage}</div>}
          {bookings.length === 0 ? (
            <p className="muted-copy">No bookings yet.</p>
          ) : (
            <div className="booking-stack">
              {bookings.map((booking) => (
                <div className="booking-card" key={booking._id}>
                  <div className="booking-head">
                    <div>
                      <strong>{booking.slotId?.label || 'Unassigned slot'}</strong>
                      <div className="muted-copy">{booking.vehicleType} | {booking.numberPlate || 'No plate'}</div>
                    </div>
                    <span className={`badge ${bookingBadge(booking.status)}`}>{booking.status}</span>
                  </div>
                  <div className="signal-list compact">
                    <div><strong>Expires</strong><span>{booking.bookingExpiresAt ? new Date(booking.bookingExpiresAt).toLocaleTimeString() : '-'}</span></div>
                    <div><strong>Duration</strong><span>{booking.durationHours}h</span></div>
                    <div><strong>Price</strong><span>Rs {booking.price}</span></div>
                  </div>
                  <div className="grid-2 booking-detail-grid">
                    <div>
                      <div className="panel-subtitle">Route Guidance</div>
                      <ol className="route-list">
                        {(booking.navigation?.steps || []).map((step, index) => (
                          <li key={`${booking._id}-${step}-${index}`}>{step}</li>
                        ))}
                      </ol>
                    </div>
                    <div>
                      <div className="panel-subtitle">QR Access</div>
                      <div
                        className="qr-frame"
                        dangerouslySetInnerHTML={{ __html: booking.qrSvg }}
                      />
                    </div>
                  </div>
                  <div className="stack-mobile">
                    {booking.status === 'PAYMENT_PENDING' && <button className="btn btn-success" onClick={() => navigate(`/payment/${booking._id}`)}>Open Payment</button>}
                    {booking.status === 'ACTIVE' && <button className="btn btn-outline" onClick={() => simulateQrEntry(booking.qrToken)}>Simulate QR Gate Entry</button>}
                    {booking.status === 'ACTIVE' && <button className="btn btn-danger" onClick={() => updateBooking(booking._id, 'checkout')}>Check Out</button>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card" style={{ marginTop: '1.5rem' }}>
          <div className="panel-title">Live Slot Map</div>
          {Object.entries(groupedSlots).map(([floor, floorSlots]) => (
            <div key={floor} style={{ marginBottom: '1.25rem' }}>
              <div className="panel-subtitle">{floor}</div>
              <div className="slot-grid">
                {floorSlots.slice(0, 24).map((slot) => (
                  <div className={`smart-slot smart-slot-${slot.status.toLowerCase()}`} key={slot._id}>
                    <div className="smart-slot-top">
                      <strong>{slot.label}</strong>
                      {slot.reservedFor !== 'none' && <span className="mini-tag">Emergency</span>}
                    </div>
                    <div className="muted-copy">Zone {slot.zone}</div>
                    <div className="slot-meta-row">
                      <span>{slot.covered ? 'Covered' : 'Open'}</span>
                      <span>{slot.shaded ? 'Shaded' : 'Sun-exposed'}</span>
                    </div>
                    <span className={`badge badge-${slot.status.toLowerCase()}`}>{slot.status}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
