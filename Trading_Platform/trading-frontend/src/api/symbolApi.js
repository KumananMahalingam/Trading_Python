import axiosInstance from './axiosInstance';

export const getAllSymbols = async () => {
    const response = await axiosInstance.get('/symbols');
    return response.data;
};
