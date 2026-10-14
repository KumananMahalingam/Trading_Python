import axiosInstance from './axiosInstance';

export const getNews = (ticker) =>
    axiosInstance.get(`/news/${ticker}`).then(r => r.data);
