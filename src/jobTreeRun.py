#!/usr/bin/env python

#Copyright (C) 2011 by Benedict Paten (benedictpaten@gmail.com)
#
#Permission is hereby granted, free of charge, to any person obtaining a copy
#of this software and associated documentation files (the "Software"), to deal
#in the Software without restriction, including without limitation the rights
#to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#copies of the Software, and to permit persons to whom the Software is
#furnished to do so, subject to the following conditions:
#
#The above copyright notice and this permission notice shall be included in
#all copies or substantial portions of the Software.
#
#THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
#THE SOFTWARE.

"""A script to setup and run a hierarchical run of cluster jobs.
"""

import os
import sys 
import xml.etree.ElementTree as ET
import cPickle
from optparse import OptionParser

from jobTree.batchSystems.parasol import ParasolBatchSystem
from jobTree.batchSystems.gridengine import GridengineBatchSystem
from jobTree.batchSystems.singleMachine import SingleMachineBatchSystem, badWorker
from jobTree.batchSystems.combinedBatchSystem import CombinedBatchSystem

from jobTree.src.master import createJob
from jobTree.src.master import mainLoop
from jobTree.src.master import writeJobs
from jobTree.src.master import getEnvironmentFileName, getJobFileDirName, getStatsFileName, getParasolResultsFileName, getConfigFileName

from sonLib.bioio import logger, setLoggingFromOptions, addLoggingOptions, getLogLevelString
from sonLib.bioio import TempFileTree
from sonLib.bioio import system

def runJobTree(command, jobTreeDir, logLevel="DEBUG", retryCount=0, batchSystem="single_machine", 
               rescueJobFrequency=None):
    """A convenience function for running job tree from within a python script.
    """
    if rescueJobFrequency != None:
        rescueJobFrequencyString = "--rescueJobsFrequency %s" % float(rescueJobFrequency)
    else:
        rescueJobFrequencyString = ""
    command = "jobTree --command \"%s\" --jobTree %s --logLevel %s \
--retryCount %i --batchSystem %s %s" % \
            (command, jobTreeDir,  logLevel, retryCount, batchSystem, rescueJobFrequencyString)
    logger.info("Running command : %s" % command)
    system(command)
    logger.info("Ran the jobtree apparently okay")
    

def commandAvailable(executable):
    return 0 == os.system("which %s > /dev/null 2> /dev/null" % executable)

def detectQueueSystem():
    if commandAvailable("parasol"):
        return "parasol"
    if commandAvailable("qstat"):
        return "gridEngine"
    return "singleMachine"

def addOptions(parser):
    addLoggingOptions(parser)#This adds the logging stuff..
    
    parser.add_option("--command", dest="command", 
                      help="The command to run (which will generate subsequent jobs)",
                      default=None)
    
    parser.add_option("--jobTree", dest="jobTree", 
                      help="Directory in which to place job management files \
(this needs to be globally accessible by all machines running jobs).\n\
If you pass an existing directory it will check if it's a valid existin job tree, then\
try and restart the jobs in it",
                      default=None)
    
    parser.add_option("--batchSystem", dest="batchSystem",
                      help="The type of batch system to run the job(s) with, currently can be 'singleMachine'/'parasol'/'acidTest'/'gridEngine'. default=%default",
                      default=detectQueueSystem())
    
    parser.add_option("--parasolCommand", dest="parasolCommand",
                      help="The command to run the parasol program default=%default",
                      default="parasol")
    
    parser.add_option("--retryCount", dest="retryCount", 
                      help="Number of times to try a failing job before giving up and labelling job failed. default=%default",
                      default=0)
    
    parser.add_option("--rescueJobsFrequency", dest="rescueJobsFrequency", 
                      help="Period of time to wait (in seconds) between checking for missing/overlong jobs (default is set by the batch system)")
    
    parser.add_option("--maxJobDuration", dest="maxJobDuration", 
                      help="Maximum runtime of a job (in seconds) before we kill it (this is an approximate time, and the actual time before killing the job may be longer). default=%default",
                      default=str(sys.maxint))
    
    parser.add_option("--jobTime", dest="jobTime", 
                      help="The approximate time (in seconds) that you'd like a list of child jobs to be run serially before being parallised. \
                      This parameter allows one to avoid over parallelising tiny jobs, and therefore paying significant scheduling overhead, by \
                      running tiny jobs in series on a single node/core of the cluster. default=%default",
                      default=30)
    
    parser.add_option("--maxLogFileSize", dest="maxLogFileSize", 
                      help="The maximum size of a log file to keep (in bytes), log files larger than this will be truncated to the last X bytes. Default is 50 kilobytes, default=%default",
                      default=50120)
    
    parser.add_option("--defaultMemory", dest="defaultMemory", 
                      help="The default amount of memory to request for a job (in bytes), by default is 2^31 = 2 gigabytes, default=%default",
                      default=2147483648)
    
    parser.add_option("--defaultCpu", dest="defaultCpu", 
                      help="The default the number of cpus to dedicate a job. default=%default",
                      default=1)
    
    parser.add_option("--maxJobs", dest="maxJobs", 
                      help="The maximum number of jobs to issue to the batch system at any one time. default=%default",
                      default=sys.maxint)
    
    parser.add_option("--maxThreads", dest="maxThreads", 
                      help="The maximum number of threads to use when running in single machine mode. default=%default",
                      default=4)
    
    parser.add_option("--stats", dest="stats", action="store_true",
                      help="Records statistics about the job-tree to be used by jobTreeStats. default=%default",
                      default=False)
    
    parser.add_option("--reportAllJobLogFiles", dest="reportAllJobLogFiles", action="store_true",
                      help="Report the log files of all jobs, not just that fail. default=%default",
                      default=False)
    
def loadTheBatchSystem(config):
    """Load the batch system.
    """
    batchSystemString = config.attrib["batch_system"]
    def batchSystemConstructionFn(batchSystemString):
        batchSystem = None
        if batchSystemString == "parasol":
            batchSystem = ParasolBatchSystem(config)
            logger.info("Using the parasol batch system")
        elif batchSystemString == "single_machine" or batchSystemString == "singleMachine":
            batchSystem = SingleMachineBatchSystem(config)
            logger.info("Using the single machine batch system")
        elif batchSystemString == "gridengine" or batchSystemString == "gridEngine":
            batchSystem = GridengineBatchSystem(config)
            logger.info("Using the grid engine machine batch system")
        elif batchSystemString == "acid_test" or batchSystemString == "acidTest":
            batchSystem = SingleMachineBatchSystem(config, workerFn=badWorker)
            config.attrib["retry_count"] = str(32) #The chance that a job does not complete after 32 goes in one in 4 billion, so you need a lot of jobs before this becomes probable
        return batchSystem
    batchSystem = batchSystemConstructionFn(batchSystemString)
    if batchSystem == None:
        batchSystems = batchSystemString.split()
        if len(batchSystems) not in (3, 4):
            raise RuntimeError("Unrecognised batch system: %s" % batchSystemString)
        maxMemoryForBatchSystem1 = float(batchSystems[2])
        maxJobs = sys.maxint
        if len(batchSystems) == 4:
            maxJobs = int(batchSystems[3])
        #Hack the max jobs argument
        oldMaxJobs = config.attrib["max_jobs"]
        config.attrib["max_jobs"] = str(maxJobs)
        batchSystem1 = batchSystemConstructionFn(batchSystems[0])
        config.attrib["max_jobs"] = str(oldMaxJobs)
        batchSystem2 = batchSystemConstructionFn(batchSystems[1])
        
        #Throw up if we can't make the batch systems
        if batchSystem1 == None or batchSystem2 == None:
            raise RuntimeError("Unrecognised batch system: %s" % batchSystemString)
        
        batchSystem = CombinedBatchSystem(config, batchSystem1, batchSystem2, lambda command, memory, cpu : memory <= maxMemoryForBatchSystem1)
    return batchSystem

def loadEnvironment(config):
    """Puts the environment in the pickle file.
    """
    #Dump out the environment of this process in the environment pickle file.
    fileHandle = open(getEnvironmentFileName(config.attrib["job_tree"]), 'w')
    cPickle.dump(os.environ, fileHandle)
    fileHandle.close()
    logger.info("Written the environment for the jobs to the environment file")

def writeConfig(config):
    #Write the config file to disk
    fileHandle = open(getConfigFileName(config.attrib["job_tree"]), 'w')
    tree = ET.ElementTree(config)
    tree.write(fileHandle)
    fileHandle.close()
    logger.info("Written the config file")

def reloadJobTree(jobTree):
    """Load the job tree from a dir.
    """
    logger.info("The job tree appears to already exist, so we'll reload it")
    assert os.path.isfile(getConfigFileName(jobTree)) #A valid job tree must contain the config file
    assert os.path.isfile(getEnvironmentFileName(jobTree)) #A valid job tree must contain a pickle file which encodes the path environment of the job
    assert os.path.isdir(getJobFileDirName(jobTree)) #A job tree must have a directory of jobs.
    
    config = ET.parse(os.path.join(jobTree, "config.xml")).getroot()
    config.attrib["log_level"] = getLogLevelString()
    writeConfig(config) #This updates the on disk config file with the new logging setting
    
    batchSystem = loadTheBatchSystem(config)
    config.attrib["job_file_tree"] = TempFileTree(getJobFileDirName(jobTree))
    logger.info("Reloaded the jobtree")
    return config, batchSystem

def createJobTree(options):
    logger.info("Starting to create the job tree setup for the first time")
    options.jobTree = os.path.abspath(options.jobTree)
    os.mkdir(options.jobTree)
    config = ET.Element("config")
    config.attrib["log_level"] = getLogLevelString()
    config.attrib["job_tree"] = options.jobTree
    config.attrib["parasol_command"] = options.parasolCommand
    config.attrib["retry_count"] = str(int(options.retryCount))
    config.attrib["max_job_duration"] = str(float(options.maxJobDuration))
    config.attrib["batch_system"] = options.batchSystem
    config.attrib["job_time"] = str(float(options.jobTime))
    config.attrib["max_log_file_size"] = str(int(options.maxLogFileSize))
    config.attrib["default_memory"] = str(int(options.defaultMemory))
    config.attrib["default_cpu"] = str(int(options.defaultCpu))
    config.attrib["max_jobs"] = str(int(options.maxJobs))
    config.attrib["max_threads"] = str(int(options.maxThreads))
    if options.reportAllJobLogFiles:
        config.attrib["reportAllJobLogFiles"] = ""
    if options.stats:
        config.attrib["stats"] = ""
        fileHandle = open(getStatsFileName(options.jobTree), 'w')
        fileHandle.write("<stats>")
        fileHandle.close()
    #Load the batch system.
    batchSystem = loadTheBatchSystem(config)
    
    #Set the parameters determining the polling frequency of the system.  
    config.attrib["rescue_jobs_frequency"] = str(float(batchSystem.getRescueJobFrequency()))
    if options.rescueJobsFrequency != None:
        config.attrib["rescue_jobs_frequency"] = str(float(options.rescueJobsFrequency))
    
    writeConfig(config)
    
    #Setup the temp file tree.
    config.attrib["job_file_tree"] = TempFileTree(getJobFileDirName(options.jobTree))
    
    logger.info("Finished the job tree setup")
    return config, batchSystem

def createFirstJob(command, config, memory=None, cpu=None, time=sys.maxint):
    """Adds the first job to to the jobtree.
    """
    logger.info("Adding the first job")
    if memory == None or memory == sys.maxint:
        memory = config.attrib["default_memory"]
    if cpu == None or cpu == sys.maxint:
        cpu = config.attrib["default_cpu"]
    job = createJob({ "command":command, "memory":str(int(memory)), "cpu":str(int(cpu)) }, None, config)
    writeJobs([job])
    logger.info("Added the first job")
    
def runJobTreeScript(options):
    """Builds the basic job tree, or takes an existing one
    and runs the job tree master script.
    """
    setLoggingFromOptions(options)
    assert options.jobTree != None #We need a job tree, or a place to create one
    if os.path.isdir(options.jobTree):
        config, batchSystem = reloadJobTree(options.jobTree)
    else:
        assert options.command != None
        config, batchSystem = createJobTree(options)
        #Setup first job.
        createFirstJob(options.command, config)
    loadEnvironment(config)
    return mainLoop(config, batchSystem)
    
def main():
    """This basic pattern can be used in your python script to avoid having to call
    the command line version of jobTree
    """
    
    ##########################################
    #Construct the arguments.
    ##########################################  
    
    parser = OptionParser()
    addOptions(parser)
    
    options, args = parser.parse_args()
    
    if len(args) != 0:
        parser.error("Unrecognised input arguments: %s" % " ".join(args))
        
    ##########################################
    #Now run the job tree construction/master
    ##########################################  
        
    runJobTreeScript(options)
    
def _test():
    import doctest      
    return doctest.testmod()

if __name__ == '__main__':
    _test()
    main()
