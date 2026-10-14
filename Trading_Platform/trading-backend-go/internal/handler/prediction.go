package handler

import (
	"errors"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/tradingapp/trading-backend/internal/apperr"
	"github.com/tradingapp/trading-backend/internal/dto"
	"github.com/tradingapp/trading-backend/internal/middleware"
	"github.com/tradingapp/trading-backend/internal/service"
)

// PredictionHandler serves the ML prediction endpoints.
type PredictionHandler struct {
	predictions *service.PredictionService
}

func NewPredictionHandler(predictions *service.PredictionService) *PredictionHandler {
	return &PredictionHandler{predictions: predictions}
}

// GetPrediction handles GET /api/predictions/{ticker}.
func (h *PredictionHandler) GetPrediction(c *gin.Context) {
	model, err := h.predictions.GetPrediction(c.Request.Context(), c.Param("ticker"))
	if err != nil {
		apperr.Respond(c, err)
		return
	}
	c.JSON(http.StatusOK, model)
}

// StartJob handles POST /api/predictions/{ticker}/jobs with {"kind": "train"|"refresh"}.
// It answers 202 for a new job, 200 when an identical job was joined, and 409
// (with the existing job) when the user already has another job in progress.
func (h *PredictionHandler) StartJob(c *gin.Context) {
	var req dto.StartPredictionJobRequest
	if err := bindJSON(c, &req); err != nil {
		apperr.Respond(c, err)
		return
	}

	job, created, err := h.predictions.StartJob(c.Request.Context(), c.Param("ticker"), req.Kind,
		middleware.CurrentUser(c))
	if err != nil {
		var conflict *service.JobConflictError
		if errors.As(err, &conflict) {
			c.AbortWithStatusJSON(http.StatusConflict, dto.PredictionConflictResponse{
				ErrorResponse: dto.ErrorResponse{
					Status:    http.StatusConflict,
					Message:   conflict.Message,
					Timestamp: time.Now().UTC(),
				},
				Job: conflict.Job,
			})
			return
		}
		apperr.Respond(c, err)
		return
	}

	status := http.StatusAccepted
	if !created {
		status = http.StatusOK
	}
	c.JSON(status, dto.PredictionJobResponse{PredictionJob: *job, Created: created})
}

// GetJob handles GET /api/prediction-jobs/{id}.
func (h *PredictionHandler) GetJob(c *gin.Context) {
	job, err := h.predictions.GetJob(c.Request.Context(), c.Param("id"))
	if err != nil {
		apperr.Respond(c, err)
		return
	}
	c.JSON(http.StatusOK, job)
}
