from pydantic import BaseModel, Field, ConfigDict
from typing import Literal

# ==========================================
# Literature Research Agent Schemas
# ==========================================

class PaperData(BaseModel):
    """
    Data contract representing a single academic paper's extracted metrics.
    Enforces strict typing and allowed values to prevent LLM hallucinations.
    """
    paper_name: str = Field(default="Unknown", alias="paper name")
    cited: str = "N/A"
    source: str = "N/A"
    year_published: str = Field(default="N/A", alias="year published")
    
    # Using aliases helps the LLM map its natural text generation to our strict keys
    data_type: str = Field(default="N/A", alias="types of available data")
    num_samples: str = Field(default="N/A", alias="number of samples")
    num_features: str = Field(default="N/A", alias="number of features")
    num_classes: str = Field(default="N/A", alias="number of classes")
    location: str = "N/A"
    duration: str = Field(default="N/A", alias="for how long")
    
    reproducible: str = Field(default="N/A", alias="is it reproducible?")
    privacy_issues: Literal["Yes", "No", "Minimal", "High", "N/A"] = Field(
        default="N/A", alias="is there privacy issues?"
    )
    data_representation: str = Field(default="N/A", alias="data representation")
    # Prompt key/schema alias previously drifted out of sync for these two fields
    # (agents/literature_research_agent.py's process_results_with_llm asked the LLM
    # for "complexity" and "can i control the application collected" — no trailing
    # "?" — neither of which matched the aliases below, so Pydantic silently applied
    # these defaults on every single paper regardless of source-data richness;
    # confirmed via both real projects' rolling CSVs, 19/19 and 12/12 rows blank).
    # The prompt now requests the exact alias strings below. Defaults changed from
    # "" to "N/A" to match every other field's default and to fail more legibly
    # (a blank cell reads as broken; "N/A" reads as "not provided") if a similar
    # key-drift regression ever happens again.
    how_complicated: str = Field(default="N/A", alias="how complicated is it?")
    can_control: str = Field(default="N/A", alias="can i control the application collected?")
    
    # Allow population by either the variable name or the alias
    model_config = ConfigDict(populate_by_name=True)

class LiteratureReport(BaseModel):
    """
    The main contract for the Literature Research Agent's output.
    Must contain a textual summary and a list of structured paper data.
    """
    summary: str = Field(..., min_length=50, description="A detailed markdown summary of the research findings.")
    papers: list[PaperData] = Field(..., min_length=1, description="List of structured data for each analyzed paper.") 


# ==========================================
# Supervisor Status Agent Schemas (NEW)
# ==========================================

class ProjectEvaluation(BaseModel):
    """
    Data contract for a single student's progress evaluation.
    Forces the LLM to choose a strict status and provide actionable insights.
    """
    project_name: str
    student_name: str = "Unknown Student"
    
    # Strict behavioral classification
    status: Literal["ON_TRACK", "NEEDS_ATTENTION", "STALLED"] = Field(
        ..., 
        description="The calculated status based strictly on the activity metrics."
    )
    
    rationale: str = Field(
        ..., 
        description="A concise 1-2 sentence explanation justifying the chosen status based on the data."
    )
    
    action_item: str = Field(
        ..., 
        description="A recommended next step for the supervisor (e.g., 'Schedule a quick check-in', 'No action needed')."
    )

class SupervisorReport(BaseModel):
    """
    The main contract for the Supervisor Status Agent's output.
    Returns a consolidated report of all projects under a single supervisor.
    """
    executive_summary: str = Field(
        ..., 
        description="A brief 2-3 sentence overview summarizing the general health of the supervisor's lab/students."
    )
    evaluations: list[ProjectEvaluation] = Field(
        ..., 
        description="A list containing the specific evaluations for each project."
    )