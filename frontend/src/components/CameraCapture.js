import React, { useEffect, useRef, useState } from 'react';

export default function CameraCapture({ label = 'Capture using camera', onCapture, buttonText = 'Capture Photo' }) {
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const streamRef = useRef(null);
  const [active, setActive] = useState(false);
  const [error, setError] = useState('');
  const [ready, setReady] = useState(false);

  useEffect(() => {
    return () => {
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((track) => track.stop());
      }
    };
  }, []);

  const startCamera = async () => {
    setError('');
    setReady(false);
    if (!navigator.mediaDevices?.getUserMedia) {
      setError('Camera access is not supported in this browser');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play().catch(() => {});
      }
      setActive(true);
    } catch (err) {
      setError(err.message || 'Camera access failed');
    }
  };

  const stopCamera = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
    setActive(false);
    setReady(false);
  };

  const capture = async () => {
    if (!videoRef.current || !canvasRef.current) return;
    const video = videoRef.current;
    if (!video.videoWidth || !video.videoHeight) {
      setError('Camera is starting. Please wait a moment and try again.');
      return;
    }
    const canvas = canvasRef.current;
    canvas.width = video.videoWidth || 1280;
    canvas.height = video.videoHeight || 720;
    const context = canvas.getContext('2d');
    if (!context) {
      setError('Canvas is not available for image capture');
      return;
    }
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.92));
    if (blob) {
      const file = new File([blob], `camera_capture_${Date.now()}.jpg`, { type: 'image/jpeg' });
      onCapture(file);
    } else {
      setError('Could not create an image from the camera feed');
    }
    stopCamera();
  };

  return (
    <div className="form-group">
      <label>{label}</label>
      {!active ? (
        <button className="btn btn-outline" type="button" onClick={startCamera}>
          Open Camera
        </button>
      ) : (
        <div style={{ display: 'grid', gap: '0.75rem', minWidth: 0 }}>
          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted
            onLoadedMetadata={() => setReady(true)}
            onCanPlay={() => setReady(true)}
            style={{ width: '100%', maxWidth: '100%', borderRadius: 10, border: '1px solid var(--border)', background: '#2b2217', aspectRatio: '4 / 3', objectFit: 'cover' }}
          />
          <div className="media-actions">
            <button className="btn btn-primary" type="button" onClick={capture} disabled={!ready}>
              {buttonText}
            </button>
            <button className="btn btn-outline" type="button" onClick={stopCamera}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {error && <div className="alert alert-error" style={{ marginTop: '0.5rem' }}>{error}</div>}
      <canvas ref={canvasRef} style={{ display: 'none' }} />
    </div>
  );
}
