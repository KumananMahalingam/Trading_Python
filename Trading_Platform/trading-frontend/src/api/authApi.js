import axios from 'axios';

const BASE_URL = 'http://localhost:8080/api/auth';

export const register = async (username, email, password) => {
    const response = await axios.post(`${BASE_URL}/register`, {
        username,
        email,
        password
    }, { withCredentials: true });
    return response.data;
};

export const login = async (email, password) => {
    const response = await axios.post(`${BASE_URL}/login`, {
        email,
        password
    }, { withCredentials: true });
    return response.data;
};

export const refreshToken = async () => {
    const response = await axios.post(`${BASE_URL}/refresh`, {}, {
        withCredentials: true
    });
    return response.data;
};

export const logout = async () => {
    await axios.post(`${BASE_URL}/logout`, {}, {
        withCredentials: true
    });
};
