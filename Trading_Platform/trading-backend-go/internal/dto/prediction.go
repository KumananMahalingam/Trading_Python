package dto

// Prediction payloads served under /api/predictions and /api/prediction-jobs.
//
// They mirror the ML service's responses with camelCase field names, like the
// rest of this API. Prices are float64: they are model outputs for display,
// not money that is booked anywhere.

// StartPredictionJobRequest is the body of POST /api/predictions/{ticker}/jobs.
type StartPredictionJobRequest struct {
	Kind string `json:"kind" binding:"required,oneof=train refresh"`
}

// NextDayPrediction is the model's forecast for the period after AsOfDate.
type NextDayPrediction struct {
	AsOfDate        string  `json:"asOfDate"`
	TargetDate      string  `json:"targetDate"`
	LastClose       float64 `json:"lastClose"`
	PredictedReturn float64 `json:"predictedReturn"`
	Sigma           float64 `json:"sigma"`
	PredictedClose  float64 `json:"predictedClose"`
	Lower           float64 `json:"lower"`
	Upper           float64 `json:"upper"`
	BandSigmas      float64 `json:"bandSigmas"`
	Direction       string  `json:"direction"`
	GeneratedAt     string  `json:"generatedAt"`
	ModelTrainedAt  *string `json:"modelTrainedAt"`
}

// BacktestPoint is one test-period day: the prediction made from BaseClose
// (the previous actual close) next to the close that actually followed.
type BacktestPoint struct {
	Date            string  `json:"date"`
	BaseClose       float64 `json:"baseClose"`
	ActualClose     float64 `json:"actualClose"`
	PredictedReturn float64 `json:"predictedReturn"`
	Sigma           float64 `json:"sigma"`
	PredictedClose  float64 `json:"predictedClose"`
	Lower           float64 `json:"lower"`
	Upper           float64 `json:"upper"`
}

// BacktestMetrics are percentages (0-100) except N and MAE (return units).
type BacktestMetrics struct {
	N                   int      `json:"n"`
	DirectionalAccuracy *float64 `json:"directionalAccuracy"`
	UpPrecision         *float64 `json:"upPrecision"`
	DownPrecision       *float64 `json:"downPrecision"`
	PredictedUpPct      *float64 `json:"predictedUpPct"`
	MajorityBaseline    *float64 `json:"majorityBaseline"`
	MAE                 *float64 `json:"mae"`
}

type Backtest struct {
	Start      *string         `json:"start"`
	End        *string         `json:"end"`
	BandSigmas float64         `json:"bandSigmas"`
	Points     []BacktestPoint `json:"points"`
	Metrics    BacktestMetrics `json:"metrics"`
}

// PredictionJob is a queued, running or finished train/refresh run.
type PredictionJob struct {
	ID            string  `json:"id"`
	Ticker        string  `json:"ticker"`
	Kind          string  `json:"kind"`
	Status        string  `json:"status"`
	Stage         *string `json:"stage"`
	StageProgress float64 `json:"stageProgress"`
	Progress      float64 `json:"progress"`
	Message       *string `json:"message"`
	Error         *string `json:"error"`
	QueuePosition *int    `json:"queuePosition"`
	EtaSeconds    *int    `json:"etaSeconds"`
	CreatedAt     string  `json:"createdAt"`
	StartedAt     *string `json:"startedAt"`
	FinishedAt    *string `json:"finishedAt"`
}

// PredictionJobResponse is returned when a job is started; Created is false
// when the request joined a job someone else had already queued.
type PredictionJobResponse struct {
	PredictionJob
	Created bool `json:"created"`
}

type PredictionEstimates struct {
	TrainSeconds   int `json:"trainSeconds"`
	RefreshSeconds int `json:"refreshSeconds"`
	QueueSeconds   int `json:"queueSeconds"`
}

// PredictionModel is GET /api/predictions/{ticker}. Status is "ready" when a
// trained model exists, "none" otherwise (Prediction and Backtest are then null).
type PredictionModel struct {
	Ticker       string              `json:"ticker"`
	AssetType    string              `json:"assetType"`
	HasNews      bool                `json:"hasNews"`
	Status       string              `json:"status"`
	TrainedAt    *string             `json:"trainedAt"`
	DataStart    *string             `json:"dataStart"`
	DataEnd      *string             `json:"dataEnd"`
	Peers        []string            `json:"peers"`
	AlphaSource  *string             `json:"alphaSource"`
	NewsArticles *int                `json:"newsArticles"`
	Prediction   *NextDayPrediction  `json:"prediction"`
	Backtest     *Backtest           `json:"backtest"`
	ActiveJob    *PredictionJob      `json:"activeJob"`
	Estimates    PredictionEstimates `json:"estimates"`
}

// PredictionConflictResponse is the 409 body when the user already has a job
// in progress; Job is that job, so the UI can follow it instead.
type PredictionConflictResponse struct {
	ErrorResponse
	Job PredictionJob `json:"job"`
}
