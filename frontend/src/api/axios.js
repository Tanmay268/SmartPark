import axios from 'axios';

const baseURL = process.env.REACT_APP_API_URL || 'http://localhost:5000';
const isNgrokUrl = /ngrok(-free)?\.app/i.test(baseURL);

const api = axios.create({
  baseURL,
});

// Attach stored token on every request
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  if (isNgrokUrl) {
    config.headers['ngrok-skip-browser-warning'] = 'true';
  }
  return config;
});

// Redirect to login on 401
api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem('token');
      window.location.href = '/login';
    }
    return Promise.reject(err);
  }
);

export default api;
