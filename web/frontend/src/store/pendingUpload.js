/**
 * Temporarily store files and requirements pending upload
 * Used for immediate redirect after clicking start engine on homepage, then making API calls on the Process page
 */
import { reactive } from 'vue'

const state = reactive({
  files: [],
  urlDocs: [],   // [{title, url, text, char_count}] fetched via /api/graph/fetch-url
  simulationRequirement: '',
  isPending: false,
  templateSeedText: '',
  templateName: ''
})

export function setPendingUpload(files, requirement, urlDocs = []) {
  state.files = files
  state.urlDocs = urlDocs
  state.simulationRequirement = requirement
  state.isPending = true
  state.templateSeedText = ''
  state.templateName = ''
}

export function setPendingTemplate(requirement, seedText, templateName) {
  state.files = []
  state.urlDocs = []
  state.simulationRequirement = requirement
  state.isPending = true
  state.templateSeedText = seedText
  state.templateName = templateName
}

export function getPendingUpload() {
  return {
    files: state.files,
    urlDocs: state.urlDocs,
    simulationRequirement: state.simulationRequirement,
    isPending: state.isPending,
    templateSeedText: state.templateSeedText,
    templateName: state.templateName
  }
}

export function clearPendingUpload() {
  state.files = []
  state.urlDocs = []
  state.simulationRequirement = ''
  state.isPending = false
  state.templateSeedText = ''
  state.templateName = ''
}

export default state
