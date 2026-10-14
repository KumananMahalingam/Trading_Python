import axios from 'axios';

const axiosInstance = axios.create({
    baseURL: 'http://localhost:8080/api',
    withCredentials: true,
});

let authToken = null;

export const setAuthToken = (token) => {
    authToken = token;
};

axiosInstance.interceptors.request.use((config) => {
    if (authToken) {
        config.headers.Authorization = `Bearer ${authToken}`;
    }
    return config;
});

export default axiosInstance;
