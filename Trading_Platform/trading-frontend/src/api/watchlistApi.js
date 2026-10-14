import axiosInstance from './axiosInstance';

export const getWatchlist = () =>
    axiosInstance.get('/watchlist').then(r => r.data);

export const addToWatchlist = (ticker) =>
    axiosInstance.post(`/watchlist/${ticker}`).then(r => r.data);

export const removeFromWatchlist = (ticker) =>
    axiosInstance.delete(`/watchlist/${ticker}`);
