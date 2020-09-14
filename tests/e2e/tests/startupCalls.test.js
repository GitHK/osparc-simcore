const auto = require('../utils/auto');
const utils = require('../utils/utils');

const {
  user,
  pass
} = utils.getUserAndPass();

beforeAll(async () => {
  await page.goto(url);
  await auto.register(page, user, pass);
  await page.waitFor(1000);
}, ourTimeout);

afterAll(async () => {
  await auto.logOut(page);
}, ourTimeout);

describe('Calls after logging in', () => {
  test('Profile', async () => {
    const responseEnv = await utils.fetch('me');
    expect(responseEnv.data["login"]).toBe(user);
  }, ourTimeout);

  test('Studies', async () => {
    const responseEnv = await utils.fetch('projects?type=user');
    expect(Array.isArray(responseEnv.data)).toBeTruthy();
  }, ourTimeout);

  test('Templates', async () => {
    const responseEnv = await utils.fetch('projects?type=template');
    expect(Array.isArray(responseEnv.data)).toBeTruthy();
  }, ourTimeout);

  test('Services', async () => {
    const responseEnv = await utils.fetch('catalog/services');
    expect(Array.isArray(responseEnv.data)).toBeTruthy();
    expect(responseEnv.data.length).toBeGreaterThan(0);
  }, ourTimeout);

  test('Locations', async () => {
    const responseEnv = await utils.fetch('storage/locations');
    expect(Array.isArray(responseEnv.data)).toBeTruthy();
    expect(responseEnv.data.length).toBeGreaterThan(0);
  }, ourTimeout);
});
