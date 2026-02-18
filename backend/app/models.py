from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON, Text, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class Review(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, index=True)
    start_url = Column(String, unique=True, index=True)
    report_year = Column(Integer)
    base_year = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    sections = relationship("Section", back_populates="review", cascade="all, delete-orphan")
    check_runs = relationship("CheckRun", back_populates="review", cascade="all, delete-orphan")
    manual_reviews = relationship("ManualReview", back_populates="review", cascade="all, delete-orphan")


class Section(Base):
    __tablename__ = "sections"

    id = Column(Integer, primary_key=True, index=True)
    review_id = Column(Integer, ForeignKey("reviews.id"))
    title = Column(String, index=True)
    url = Column(String, index=True)
    anchor = Column(String, nullable=True)
    level = Column(Integer, default=1)
    is_virtual = Column(Boolean, default=False)  # Para "page" mode
    
    review = relationship("Review", back_populates="sections")
    check_runs = relationship("CheckRun", back_populates="section", cascade="all, delete-orphan")
    manual_reviews = relationship("ManualReview", back_populates="section", cascade="all, delete-orphan")


class CheckRun(Base):
    __tablename__ = "check_runs"

    id = Column(Integer, primary_key=True, index=True)
    review_id = Column(Integer, ForeignKey("reviews.id"))
    section_id = Column(Integer, ForeignKey("sections.id"), nullable=True)
    mode = Column(String)  # "section" ou "page"
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    
    review = relationship("Review", back_populates="check_runs")
    section = relationship("Section", back_populates="check_runs")
    results = relationship("CheckResult", back_populates="check_run", cascade="all, delete-orphan")


class CheckResult(Base):
    __tablename__ = "check_results"

    id = Column(Integer, primary_key=True, index=True)
    checkrun_id = Column(Integer, ForeignKey("check_runs.id"))
    rule = Column(String, index=True)
    severity = Column(String)  # "PASS", "WARN", "FAIL"
    message = Column(Text)
    evidence_json = Column(JSON, nullable=True)
    
    check_run = relationship("CheckRun", back_populates="results")


class ManualReview(Base):
    __tablename__ = "manual_reviews"

    id = Column(Integer, primary_key=True, index=True)
    review_id = Column(Integer, ForeignKey("reviews.id"))
    section_id = Column(Integer, ForeignKey("sections.id"))
    items_checked_json = Column(JSON, nullable=True)
    comments = Column(Text, nullable=True)
    reviewer = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    review = relationship("Review", back_populates="manual_reviews")
    section = relationship("Section", back_populates="manual_reviews")
