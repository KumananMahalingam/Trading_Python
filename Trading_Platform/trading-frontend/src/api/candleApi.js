import axiosInstance from './axiosInstance';

export const getCandles = async (ticker, interval = '1D') => {
    const response = await axiosInstance.get(`/candles/${ticker}`, {
        params: { interval },
    });
    return response.data;
};
