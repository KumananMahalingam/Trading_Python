import axiosInstance from './axiosInstance';

// Stored model prediction + backtest for a ticker ({ status: 'ready' | 'none', ... }).
export const getPrediction = async (ticker) => {
    const response = await axiosInstance.get(`/predictions/${encodeURIComponent(ticker)}`);
    return response.data;
};

// Start (or join) a model run. kind: 'train' | 'refresh'.
export const startPredictionJob = async (ticker, kind) => {
    const response = await axiosInstance.post(`/predictions/${encodeURIComponent(ticker)}/jobs`, { kind });
    return response.data;
};

export const getPredictionJob = async (jobId) => {
    const response = await axiosInstance.get(`/prediction-jobs/${encodeURIComponent(jobId)}`);
    return response.data;
};
