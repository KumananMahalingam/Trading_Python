import axiosInstance from './axiosInstance';

export const getMyProfile = () =>
    axiosInstance.get('/user/me').then(r => r.data);
