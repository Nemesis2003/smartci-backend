from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import subprocess
import json
import shutil
import tempfile
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="SmartCI API")

# CORS - Allow your Vercel frontend to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with your Vercel domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AnalyzeRequest(BaseModel):
    repo_url: str

class AnalyzeResponse(BaseModel):
    success: bool
    repo_name: str
    current_time: int
    smart_time: int
    savings_percent: int
    commits_analyzed: int
    tests_total: int
    tests_avg_selected: int
    monthly_savings: str
    error: str = None

@app.get("/")
def read_root():
    return {
        "message": "SmartCI API is running!",
        "endpoints": ["/analyze", "/health"]
    }

@app.get("/health")
def health_check():
    return {"status": "healthy"}

@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze_repo(request: AnalyzeRequest):
    """
    Analyze a GitHub repository and calculate CI savings
    """
    repo_url = request.repo_url
    
    # Validate GitHub URL
    if not repo_url or "github.com" not in repo_url:
        raise HTTPException(status_code=400, detail="Invalid GitHub URL")
    
    logger.info(f"Analyzing repo: {repo_url}")
    
    # Extract repo info
    parts = repo_url.rstrip('/').split('/')
    owner = parts[-2]
    repo_name = parts[-1].replace('.git', '')
    
    # Create temporary directory
    temp_dir = tempfile.mkdtemp(prefix=f"smartci_{owner}_{repo_name}_")
    
    try:
        # Clone repository
        logger.info(f"Cloning to {temp_dir}...")
        clone_result = subprocess.run(
            ['git', 'clone', '--depth', '50', repo_url, temp_dir],
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if clone_result.returncode != 0:
            raise HTTPException(
                status_code=400, 
                detail=f"Failed to clone repository: {clone_result.stderr}"
            )
        
        # Get commits
        commits_result = subprocess.run(
            ['git', '-C', temp_dir, 'log', '--format=%H', '-n', '30'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        commits = [c for c in commits_result.stdout.strip().split('\n') if c]
        
        if len(commits) < 2:
            raise HTTPException(
                status_code=400,
                detail="Repository must have at least 2 commits"
            )
        
        logger.info(f"Found {len(commits)} commits")
        
        # Analyze commits
        total_current_time = 0
        total_smart_time = 0
        total_tests = 0
        total_selected = 0
        analyzed = 0
        
        for i in range(min(len(commits) - 1, 20)):
            head_sha = commits[i]
            base_sha = commits[i + 1]
            
            try:
                # Run Smart CI analysis
                result = subprocess.run(
                    [
                        'python', 'smart_ci.py', 'analyze',
                        '--repo', temp_dir,
                        '--base-sha', base_sha,
                        '--head-sha', head_sha
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    cwd=Path(__file__).parent  # Make sure we run from backend directory
                )
    
                # Debug output
                if result.stdout:
                    logger.info(f"Smart CI output: {result.stdout[:200]}")
                if result.stderr:
                    logger.warning(f"Smart CI stderr: {result.stderr[:200]}")
    
                if result.returncode == 0 and result.stdout.strip():
                    try:
                        analysis = json.loads(result.stdout)
                    except json.JSONDecodeError as e:
                        logger.warning(f"JSON decode error: {e}, output was: {result.stdout[:200]}")
                        continue
                    
                    if analysis.get('success'):
                        # Simulate test counts and timing
                        total_test_count = 1000 + (i * 50)
                        
                        if analysis['analysis_mode'] == 'smart_selection':
                            func_count = sum(len(funcs) for funcs in analysis.get('changed_functions', {}).values())
                            selected = max(50, func_count * 15)
                        elif analysis['analysis_mode'] == 'no_changes':
                            selected = 0
                        else:
                            selected = total_test_count
                        
                        # Simulate timing (20-30 min baseline)
                        base_time = 1200 + (i * 30)
                        ratio = selected / total_test_count if total_test_count > 0 else 0
                        smart_time = int(base_time * ratio)
                        
                        total_current_time += base_time
                        total_smart_time += smart_time
                        total_tests += total_test_count
                        total_selected += selected
                        analyzed += 1
                
            except Exception as e:
                logger.warning(f"Skipping commit {head_sha[:7]}: {str(e)}")
                continue
        
        if analyzed == 0:
            raise HTTPException(
                status_code=400,
                detail="No commits could be analyzed"
            )
        
        # Calculate results
        avg_current = total_current_time // analyzed
        avg_smart = total_smart_time // analyzed
        savings_pct = int(((avg_current - avg_smart) / avg_current) * 100)
        avg_tests = total_tests // analyzed
        avg_selected = total_selected // analyzed
        
        # Calculate monthly savings
        # 100 engineers, $150k salary, 10 commits/day, 20 work days
        minutes_saved = (avg_current - avg_smart) / 60
        daily_savings = minutes_saved * 10 * 100
        monthly_savings = daily_savings * 20
        hourly_rate = 150000 / (52 * 40)
        dollar_savings = int((monthly_savings / 60) * hourly_rate)
        
        logger.info(f"Analysis complete: {analyzed} commits, {savings_pct}% savings")
        
        return AnalyzeResponse(
            success=True,
            repo_name=repo_name,
            current_time=avg_current,
            smart_time=avg_smart,
            savings_percent=savings_pct,
            commits_analyzed=analyzed,
            tests_total=avg_tests,
            tests_avg_selected=avg_selected,
            monthly_savings=f"${dollar_savings:,}"
        )
        
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="Analysis timed out")
    
    except Exception as e:
        logger.error(f"Analysis error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    
    finally:
        # Cleanup
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.info("Cleaned up temp directory")
        except Exception as e:
            logger.warning(f"Cleanup failed: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)



## Your backend folder should now look like:
