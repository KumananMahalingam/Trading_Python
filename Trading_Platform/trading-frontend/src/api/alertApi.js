import axiosInstance from './axiosInstance';

export const getAlerts = () =>
    axiosInstance.get('/alerts').then(r => r.data);

export const createAlert = (ticker, targetPrice, condition) =>
    axiosInstance.post('/alerts', { ticker, targetPrice, condition }).then(r => r.data);

export const cancelAlert = (id) =>
    axiosInstance.delete(`/alerts/${id}/cancel`);

export const deleteAlert = (id) =>
    axiosInstance.delete(`/alerts/${id}`);
