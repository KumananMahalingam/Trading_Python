import axiosInstance from './axiosInstance';

export const getPortfolio = () =>
    axiosInstance.get('/portfolio').then(r => r.data);
