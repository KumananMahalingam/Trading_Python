import axiosInstance from './axiosInstance';

export const buyStock = (ticker, quantity, price) =>
    axiosInstance.post('/trades/buy', { ticker, quantity, price }).then(r => r.data);

export const sellStock = (ticker, quantity, price) =>
    axiosInstance.post('/trades/sell', { ticker, quantity, price }).then(r => r.data);

export const getTradeHistory = () =>
    axiosInstance.get('/trades/history').then(r => r.data);
