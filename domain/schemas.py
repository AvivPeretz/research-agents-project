from pydantic import BaseModel, Field, ConfigDict
from typing import Literal

class PaperData(BaseModel):
    """
    Data contract representing a single academic paper's extracted metrics.
    Enforces strict typing and allowed values to prevent LLM hallucinations.
    """
    paper_name: str
    cited: str = "N/A"
    source: str = "N/A"
    year_published: str = "N/A"
    
    # Using aliases helps the LLM map its natural text generation to our strict keys
    data_type: str = Field(default="N/A", alias="types of available data")
    num_samples: str = Field(default="N/A", alias="number of samples")
    num_features: str = Field(default="N/A", alias="number of features")
    num_classes: str = Field(default="N/A", alias="number of classes")
    location: str = "N/A"
    duration: str = Field(default="N/A", alias="for how long")
    
    # Enforcing strict choices (Enums)
    reproducible: Literal["Yes", "No", "N/A"] = "N/A"
    complexity: Literal["High", "Moderate", "Low", "N/A"] = "N/A"
    privacy_issues: Literal["Yes", "No", "Minimal", "High", "N/A"] = Field(
        default="N/A", alias="is there privacy issues?"
    )
    controllable: Literal["Yes", "No", "N/A"] = Field(
        default="N/A", alias="can i control the application collected"
    )
    
    # Allow population by either the variable name or the alias
    model_config = ConfigDict(populate_by_name=True)

class LiteratureReport(BaseModel):
    """
    The main contract for the Literature Research Agent's output.
    Must contain a textual summary and a list of structured paper data.
    """
    summary: str = Field(..., min_length=50, description="A detailed markdown summary of the research findings.")
    papers: list[PaperData] = Field(..., min_length=1, description="List of structured data for each analyzed paper.")