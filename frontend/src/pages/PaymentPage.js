import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import api from '../api/axios';
import { connectLiveEvents } from '../api/live';
import { useAuth } from '../context/AuthContext';

export default function PaymentPage() {
  const { bookingId } = useParams();
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [booking, setBooking] = useState(null);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [socketStatus, setSocketStatus] = useState(false);

  const loadBooking = async () => {
    const res = await api.get(`/bookings/${bookingId}`);
    setBooking(res.data.booking);
    return res.data.booking;
  };

  useEffect(() => {
    let active = true;

    loadBooking()
      .catch((error) => {
        if (active) {
          setMessage(error.response?.data?.message || 'Unable to load payment details');
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });

    const disconnect = connectLiveEvents({
      onStatus: ({ connected }) => setSocketStatus(connected),
      onEvent: async () => {
        if (!active) {
          return;
        }
        try {
          const refreshed = await loadBooking();
          if (refreshed.status === 'ACTIVE') {
            navigate('/dashboard');
          }
        } catch {
          // Ignore transient polling issues on the payment screen.
        }
      }
    });

    return () => {
      active = false;
      disconnect();
    };
  }, [bookingId, navigate]);

  useEffect(() => {
    if (booking?.status === 'ACTIVE' || booking?.status === 'COMPLETED') {
      navigate('/dashboard');
    }
  }, [booking, navigate]);

  const completePayment = async () => {
    try {
      setSubmitting(true);
      const res = await api.post(`/bookings/${bookingId}/payment/complete`);
      setBooking(res.data.booking);
      setMessage(res.data.message);
      navigate('/dashboard');
    } catch (error) {
      setMessage(error.response?.data?.message || 'Payment confirmation failed');
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return <div className="loader">Loading payment...</div>;
  }

  return (
    <div>
      <nav className="navbar">
        <span className="navbar-brand">SmartPark Payments</span>
        <div className="navbar-user">
          <span>{user?.name}</span>
          <span className={`live-pill ${socketStatus ? 'live-pill-on' : ''}`}>{socketStatus ? 'Live' : 'Offline'}</span>
          <button className="btn btn-outline" onClick={logout}>Logout</button>
        </div>
      </nav>

      <div className="page payment-page">
        <div className="payment-shell">
          <div className="card payment-card">
            <div className="eyebrow">Vehicle Detected</div>
            <h1>Complete payment to open the gate</h1>
            <p className="muted-copy">
              The slot stays reserved until payment is confirmed. For development, use the manual button below to simulate a successful payment.
            </p>

            {message && (
              <div className={`alert ${message.toLowerCase().includes('failed') || message.toLowerCase().includes('unable') ? 'alert-error' : 'alert-success'}`}>
                {message}
              </div>
            )}

            {booking ? (
              <div className="grid-2 payment-grid">
                <div className="payment-details">
                  <div className="signal-list">
                    <div><strong>Vehicle</strong><span>{booking.vehicleType} / {booking.numberPlate}</span></div>
                    <div><strong>Reserved Slot</strong><span>{booking.slotId?.label || '-'}</span></div>
                    <div><strong>Duration</strong><span>{booking.durationHours}h</span></div>
                    <div><strong>Amount</strong><span>Rs {booking.price}</span></div>
                    <div><strong>Reference</strong><span>{booking.paymentReference}</span></div>
                    <div><strong>Status</strong><span>{booking.status}</span></div>
                  </div>
                </div>

                <div className="payment-qr-panel">
                  <div className="panel-subtitle">Payment QR</div>
                  <div
                    className="qr-frame payment-qr-frame"
                    dangerouslySetInnerHTML={{ __html: booking.paymentQrSvg }}
                  />
                  <p className="muted-copy payment-caption">Scan this QR in a real payment flow, or use the manual development button below.</p>
                </div>
              </div>
            ) : (
              <p className="muted-copy">Booking details are unavailable.</p>
            )}

            <div className="stack-mobile payment-actions">
              <button className="btn btn-success" onClick={completePayment} disabled={submitting || !booking || booking.status !== 'PAYMENT_PENDING'}>
                {submitting ? 'Processing...' : 'Done'}
              </button>
              <button className="btn btn-outline" onClick={() => navigate('/dashboard')}>Back to Dashboard</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
